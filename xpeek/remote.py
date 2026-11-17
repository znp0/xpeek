"""Session-scoped remote browser viewer; HTTP threads never access GTK objects."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import secrets
import socket
import struct
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from queue import SimpleQueue
from threading import Condition, Lock, Thread

DEFAULT_PORT = 38853


def remote_notice(message):
    logging.getLogger("xpeek.remote").info("xpeek remote [%s]: %s", os.getpid(), message)
    try:
        os.write(2, f"xpeek remote [{os.getpid()}]: {message}\n".encode())
    except OSError:
        pass

PAGE = files(__package__).joinpath("remote.html").read_text(encoding="utf-8")


def _android_model(user_agent: str) -> str | None:
    section = re.search(r"\([^)]*\bAndroid\b[^)]*\)", user_agent, re.IGNORECASE)
    if section is None:
        return None
    parts = section.group()[1:-1].split(";")
    for index, part in enumerate(parts):
        if not re.search(r"\bAndroid\b", part, re.IGNORECASE):
            continue
        for field in parts[index + 1:]:
            model = re.split(r"\s+Build[/ ]", field.strip(), maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if model.lower() in {"k", "u", "wv", "mobile", "tablet", "phone", "unknown", "generic", "armv7l", "armv8l", "aarch64", "arm64", "x86", "x86_64"}:
                continue
            if re.fullmatch(r"[a-z]{2,3}[-_][a-z]{2}", model, re.IGNORECASE):
                continue
            if re.fullmatch(r"[\w .+\-]{1,64}", model) and any(char.isalnum() for char in model):
                return model
        break
    return None


def client_label(user_agent: str) -> str:
    """Infer a readable browser/platform label without retaining the raw user agent."""
    agent = user_agent[:512].lower()
    browser = "Web browser"
    for name, markers in (
        ("Edge", ("edg/", "edga/", "edgios/")),
        ("Opera", ("opr/", "opt/")),
        ("Samsung Internet", ("samsungbrowser/",)),
        ("Firefox", ("firefox/", "fxios/")),
        ("Chromium", ("chromium/",)),
        ("Chrome", ("chrome/", "crios/")),
        ("Safari", ("safari/",)),
    ):
        if any(marker in agent for marker in markers):
            browser = name
            break
    platform = None
    for name, markers in (
        ("iPhone", ("iphone",)),
        ("iPad", ("ipad",)),
        ("Android", ("android",)),
        ("Windows", ("windows",)),
        ("ChromeOS", ("cros",)),
        ("macOS", ("macintosh", "mac os x")),
        ("Linux", ("linux",)),
    ):
        if any(marker in agent for marker in markers):
            platform = name
            break
    if platform == "Android":
        platform = _android_model(user_agent[:512]) or platform
    if platform == "macOS" and "mobile/" in agent:
        platform = "iPad"
    return f"{browser} on {platform}" if platform else browser


class RemoteServer:
    def __init__(self, port: int = DEFAULT_PORT):
        self._tokens = set()
        self._pairings = {}
        self.lock = Lock()
        self.changed = Condition(self.lock)
        self.revision = 0
        self.actions = SimpleQueue()
        self.last_seen = 0.0
        self.snapshot = {"remote": False, "entries": []}
        self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *_args):
                pass

            def reply(self, code, data, content_type="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                supplied = self.headers.get("Authorization", "")
                with owner.lock:
                    return not owner.closed and any(secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()) for token in owner._tokens)

            def do_GET(self):
                if self.path == "/":
                    self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
                elif self.path == "/pair-events":
                    self.pair_events()
                elif not self.authorized():
                    self.reply(401, b'{}')
                elif self.path == "/events":
                    self.events()
                else:
                    self.reply(404, b'{}')

            def pair_events(self):
                supplied = self.headers.get("Authorization", "")
                with owner.changed:
                    owner._expire_pairings()
                    ticket = next((key for key in owner._pairings if secrets.compare_digest(supplied.encode(), ("Bearer " + key).encode())), None)
                if ticket is None:
                    self.reply(401, b'{}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    while True:
                        with owner.changed:
                            owner._expire_pairings()
                            pairing = owner._pairings.get(ticket)
                            if owner.closed or pairing is None:
                                result = {"status": "expired"}
                            else:
                                result = {"status": pairing["status"]}
                                if pairing["status"] == "approved":
                                    result["token"] = pairing["token"]
                            frame = ("data: " + json.dumps(result) + "\n\n").encode()
                        self.wfile.write(frame)
                        self.wfile.flush()
                        if result["status"] != "pending":
                            return
                        with owner.changed:
                            owner.changed.wait_for(lambda: owner.closed or ticket not in owner._pairings or owner._pairings[ticket]["status"] != "pending", timeout=5)
                except OSError:
                    return

            def events(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                revision = -1
                try:
                    while True:
                        with owner.changed:
                            owner.changed.wait_for(lambda revision=revision: owner.closed or owner.revision != revision, timeout=5)
                            if owner.closed:
                                frame = b"event: end\ndata: {}\n\n"
                            elif owner.revision != revision:
                                revision = owner.revision
                                frame = ("data: " + json.dumps(owner.snapshot, ensure_ascii=False) + "\n\n").encode()
                            else:
                                frame = b": heartbeat\n\n"
                            closed = owner.closed
                        self.wfile.write(frame)
                        self.wfile.flush()
                        if closed:
                            return
                except OSError:
                    return

            def do_POST(self):
                if self.path == "/pair":
                    if self.headers.get("X-Xpeek-Pairing") != "1":
                        self.reply(400, b'{}')
                        return
                    result = owner.request_pairing(self.client_address[0], self.headers.get("User-Agent", ""))
                    self.reply(200 if result else 429, json.dumps(result or {}).encode())
                elif not self.authorized():
                    self.reply(401, b'{}')
                elif self.path == "/heartbeat":
                    with owner.lock:
                        owner.last_seen = time.monotonic()
                    self.reply(200, b'{}')
                elif self.path in ("/activate", "/desktop", "/end"):
                    owner.actions.put(self.path[1:])
                    self.reply(200, b'{}')
                else:
                    self.reply(404, b'{}')

        self.http = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.http.daemon_threads = True
        self.thread = Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        remote_notice(f"listening on TCP {self.http.server_port}")

    def _expire_pairings(self):
        now = time.monotonic()
        for ticket, pairing in list(self._pairings.items()):
            if pairing["expires"] <= now:
                if pairing["status"] == "pending":
                    pairing["status"] = "expired"
                    pairing["expires"] = now + 30
                else:
                    del self._pairings[ticket]
                self.changed.notify_all()

    def request_pairing(self, address, user_agent=""):
        with self.changed:
            self._expire_pairings()
            if self.closed or len(self._pairings) >= 32 or len(self._tokens) >= 32 or sum(p["status"] == "pending" for p in self._pairings.values()) >= 4:
                return None
            ticket = secrets.token_urlsafe(32)
            codes = {p["code"] for p in self._pairings.values()}
            code = f"{secrets.randbelow(1000000):06d}"
            while code in codes:
                code = f"{secrets.randbelow(1000000):06d}"
            self._pairings[ticket] = {"code": code, "address": address, "label": client_label(user_agent), "status": "pending", "expires": time.monotonic() + 120}
            self.changed.notify_all()
            return {"ticket": ticket, "code": code}

    def pending_requests(self):
        with self.changed:
            self._expire_pairings()
            return [{"id": ticket, "code": p["code"], "address": p["address"], "label": p["label"]} for ticket, p in self._pairings.items() if p["status"] == "pending"]

    def decide_pairing(self, ticket, approve):
        with self.changed:
            self._expire_pairings()
            pairing = self._pairings.get(ticket)
            if self.closed or pairing is None or pairing["status"] != "pending":
                return False
            pairing["status"] = "approved" if approve else "rejected"
            pairing["expires"] = time.monotonic() + 60
            if approve:
                token = secrets.token_urlsafe(32)
                pairing["token"] = token
                self._tokens.add(token)
            self.changed.notify_all()
            return True

    def publish(self, snapshot):
        with self.changed:
            if self.closed or snapshot == self.snapshot:
                return
            self.snapshot = snapshot
            self.revision += 1
            self.changed.notify_all()

    def links(self):
        addresses = set()
        preferred = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                preferred = probe.getsockname()[0]
                addresses.add(preferred)
        except OSError:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                for _index, name in socket.if_nameindex():
                    try:
                        request = struct.pack("256s", name.encode()[:15])
                        response = fcntl.ioctl(probe.fileno(), 0x8915, request)  # Linux SIOCGIFADDR.
                        addresses.add(socket.inet_ntoa(response[20:24]))
                    except OSError:
                        continue
        except OSError:
            pass
        try:
            addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
        addresses = sorted((a for a in addresses if not a.startswith("127.") and a != "0.0.0.0"), key=lambda a: (a != preferred, a))
        return [f"http://{address}:{self.http.server_port}/" for address in addresses]

    def close(self, reason="session ended"):
        with self.changed:
            if self.closed:
                return
            self.closed = True
            self._tokens.clear()
            self.changed.notify_all()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=1)
        remote_notice(f"stopped TCP {self.http.server_port}: {reason}")
