"""Stop the session's popup and owned workers before replacing its installation."""
from __future__ import annotations

import os
import select
import signal
import time
from contextlib import ExitStack
from pathlib import Path

from . import APPLICATION_ID

SHUTDOWN_TIMEOUT = 3


def _wait_processes(handles: dict[int, int], timeout: float) -> bool:
    pending = set(handles.values())
    deadline = time.monotonic() + timeout
    while pending:
        ready, _, _ = select.select(list(pending), [], [], max(0, deadline - time.monotonic()))
        pending.difference_update(ready)
        if pending and time.monotonic() >= deadline:
            return False
    return True


def _send(handle: int, sig: int) -> None:
    try:
        signal.pidfd_send_signal(handle, sig)
    except ProcessLookupError:
        pass


def _children(pid: int) -> set[int]:
    children = set()
    for task in (Path("/proc") / str(pid) / "task").glob("*"):
        try:
            children.update(map(int, (task / "children").read_text().split()))
        except FileNotFoundError:
            pass  # A thread or process exited during inspection.
    return children


def _freeze(pid: int, handle: int) -> None:
    _send(handle, signal.SIGSTOP)
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT
    while not _wait_processes({pid: handle}, 0):
        try:
            state = next(line.split()[1] for line in (Path("/proc") / str(pid) / "status").read_text().splitlines()
                         if line.startswith("State:"))
            if state in ("T", "t"):
                return
        except FileNotFoundError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Could not suspend blocked popup work; update was cancelled.")
        time.sleep(0.01)


def _collect_children(handles: dict[int, int], stack: ExitStack, *, freeze: bool = False) -> None:
    pending = list(handles)
    visited = set()
    while pending:
        parent = pending.pop()
        if parent in visited or _wait_processes({parent: handles[parent]}, 0):
            continue
        visited.add(parent)
        if freeze:
            _freeze(parent, handles[parent])
        for pid in _children(parent):
            if pid in handles:
                pending.append(pid)
                continue
            try:
                handle = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            stack.callback(os.close, handle)
            # Confirm it still belongs to this parent after opening the pidfd.
            # Signals then target that process even if its numeric PID is reused.
            if pid in _children(parent) and not _wait_processes({parent: handles[parent]}, 0):
                handles[pid] = handle
                pending.append(pid)


def stop_running_popup() -> bool:
    """Request clean shutdown, then terminate blocked work within a bounded wait.

    Target the existing session D-Bus owner without activating a new popup or
    loading configuration. Wait for process exit, not merely D-Bus unregister,
    because executor and Paddle cleanup can continue after GTK stops running.
    """
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not Path(f"/run/user/{os.getuid()}/bus").exists():
        return False
    try:
        from gi.repository import Gio, GLib
    except ImportError as exc:
        raise RuntimeError("Stopping the popup requires python-gobject; install it before updating.") from exc

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def bus_call(method, parameters):
            return connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", method,
                parameters, None, Gio.DBusCallFlags.NONE, SHUTDOWN_TIMEOUT * 1000, None,
            ).unpack()[0]

        if not bus_call("NameHasOwner", GLib.Variant("(s)", (APPLICATION_ID,))):
            return False
        owner = bus_call("GetNameOwner", GLib.Variant("(s)", (APPLICATION_ID,)))
        pid = bus_call("GetConnectionUnixProcessID", GLib.Variant("(s)", (owner,)))
        with ExitStack() as stack:
            try:
                handle = os.pidfd_open(pid)
            except ProcessLookupError:
                return False
            stack.callback(os.close, handle)
            # Recheck the unique bus owner after opening the pidfd so a racing
            # exit and numeric PID reuse cannot select another process.
            if bus_call("GetConnectionUnixProcessID", GLib.Variant("(s)", (owner,))) != pid:
                raise RuntimeError("The popup owner changed; retry updating.")
            handles = {pid: handle}
            _collect_children(handles, stack)
            try:
                connection.call_sync(
                    owner, "/" + APPLICATION_ID.replace(".", "/"), "org.freedesktop.Application", "ActivateAction",
                    GLib.Variant("(sava{sv})", ("shutdown", [], {})), None,
                    Gio.DBusCallFlags.NONE, SHUTDOWN_TIMEOUT * 1000, None,
                )
            except GLib.Error:
                # Older or unresponsive popups may not accept the action.
                # The captured pidfds still identify their owned processes.
                pass
            if _wait_processes(handles, SHUTDOWN_TIMEOUT):
                return True
            try:
                # Freeze the surviving process tree before collecting new
                # children so blocked work cannot spawn another OCR worker.
                _collect_children(handles, stack, freeze=True)
                for process in reversed(list(handles.values())):
                    _send(process, signal.SIGKILL)
                if not _wait_processes(handles, SHUTDOWN_TIMEOUT):
                    raise RuntimeError("The popup or OCR worker did not stop; update was cancelled.")
            finally:
                for process in handles.values():
                    _send(process, signal.SIGCONT)
            return True
    except GLib.Error as exc:
        if exc.matches(Gio.dbus_error_quark(), Gio.DBusError.NAME_HAS_NO_OWNER):
            return False  # The popup exited between the owner/PID queries.
        raise RuntimeError("Could not stop the running popup; update was cancelled.") from exc
    except OSError as exc:
        raise RuntimeError("Could not stop the running popup; update was cancelled.") from exc
