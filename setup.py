#!/usr/bin/env python3
"""Install or uninstall xpeek for the current user."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
MARKER = VENV_DIR / ".xpeek-install.json"
BIN_DIR = Path.home() / ".local" / "bin"
LINK = BIN_DIR / "xpeek"
EXECUTABLE = VENV_DIR / "bin" / "xpeek"


def read_state() -> dict:
    if VENV_DIR.is_symlink() or MARKER.is_symlink() or not MARKER.is_file():
        raise RuntimeError(f"{VENV_DIR} is not an environment managed by this installer.")
    state = json.loads(MARKER.read_text())
    if state.get("project") != str(PROJECT_DIR) or state.get("link") != str(LINK):
        raise RuntimeError("Installation belongs to a different project path or user.")
    return state


def matching_link() -> bool:
    return LINK.is_symlink() and LINK.readlink() == EXECUTABLE


def uninstall() -> None:
    if not VENV_DIR.exists() and not VENV_DIR.is_symlink():
        print("xpeek is not installed by this setup script.")
        return
    state = read_state()
    if matching_link():
        LINK.unlink()
    shutil.rmtree(VENV_DIR)
    for directory in state["created_dirs"]:
        try:
            Path(directory).rmdir()
        except OSError:
            pass  # Keep directories that now contain other files.
    print("Uninstalled xpeek. User configuration and history were preserved.")


def install() -> None:
    if sys.platform != "linux":
        raise RuntimeError("xpeek currently requires Linux and a Wayland desktop.")
    existing = VENV_DIR.exists() or VENV_DIR.is_symlink()
    if (LINK.exists() or LINK.is_symlink()) and (not existing or not matching_link()):
        raise RuntimeError(f"Refusing to overwrite an existing command: {LINK}")

    state = read_state() if existing else {
        "project": str(PROJECT_DIR), "link": str(LINK), "created_dirs": [],
    }
    try:
        if not existing:
            print("Creating virtual environment...", flush=True)
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
            MARKER.write_text(json.dumps(state, indent=2) + "\n")

        # Build from a temporary source copy to keep setuptools artifacts
        # and this installer script out of package builds.
        with tempfile.TemporaryDirectory(prefix="xpeek-build-") as directory:
            source = Path(directory)
            for name in ("pyproject.toml", "README.md"):
                shutil.copy2(PROJECT_DIR / name, source / name)
            shutil.copytree(
                PROJECT_DIR / "xpeek", source / "xpeek",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            print("Installing xpeek and provider dependencies...", flush=True)
            subprocess.run(
                [str(VENV_DIR / "bin" / "python"), "-m", "pip", "install",
                 "--upgrade", f"{source}[all]"],
                check=True,
            )

        missing_dirs = []
        directory = BIN_DIR
        while not directory.exists():
            missing_dirs.append(str(directory))
            directory = directory.parent
        state["created_dirs"] = list(dict.fromkeys(state["created_dirs"] + missing_dirs))
        MARKER.write_text(json.dumps(state, indent=2) + "\n")
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        if not matching_link():
            LINK.symlink_to(EXECUTABLE)
    except Exception:
        if not existing:
            if MARKER.is_file():
                uninstall()
            elif VENV_DIR.is_dir():
                shutil.rmtree(VENV_DIR)
        raise

    print(f"Installed xpeek: {LINK}")
    if str(BIN_DIR) not in os.environ.get("PATH", "").split(os.pathsep):
        print('Add ~/.local/bin to your PATH: export PATH="$HOME/.local/bin:$PATH"')
    missing_tools = [name for name in ("grim", "slurp", "wl-copy") if not shutil.which(name)]
    if missing_tools:
        print("Missing system tools: " + ", ".join(missing_tools))
        print("On Arch/CachyOS: sudo pacman -S grim slurp wl-clipboard")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or uninstall xpeek for this user.")
    parser.add_argument("command", choices=("install", "uninstall"))
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    try:
        {"install": install, "uninstall": uninstall}[args.command]()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # setuptools executes setup.py while building a pyproject-based package.
    # Keep standard wheel/sdist builds working alongside our installer CLI.
    if "setuptools.build_meta" in sys.modules:
        from setuptools import setup
        setup()
    else:
        raise SystemExit(main())
