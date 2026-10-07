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
from dataclasses import asdict

from xpeek.config import Config, CONFIG_PATH

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


def create_config(state: dict) -> bool:
    """Write initial settings once, preserving existing user configuration."""
    state["config_path"] = str(CONFIG_PATH)
    if CONFIG_PATH.exists() or CONFIG_PATH.is_symlink():
        MARKER.write_text(json.dumps(state, indent=2) + "\n")
        return False
    missing_dirs = []
    directory = CONFIG_PATH.parent
    while not directory.exists():
        missing_dirs.append(str(directory))
        directory = directory.parent
    state["created_dirs"] = list(dict.fromkeys(state["created_dirs"] + missing_dirs))
    MARKER.write_text(json.dumps(state, indent=2) + "\n")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(asdict(Config()), handle, indent=2)
            handle.write("\n")
    except Exception:
        CONFIG_PATH.unlink(missing_ok=True)
        raise
    return True


def uninstall(remove_config: bool = False) -> None:
    state = {"created_dirs": [], "config_path": str(CONFIG_PATH)}
    if not VENV_DIR.exists() and not VENV_DIR.is_symlink():
        print("xpeek is not installed by this setup script.")
    else:
        state = read_state()
        if matching_link():
            LINK.unlink()
        shutil.rmtree(VENV_DIR)
    if remove_config:
        config_path = Path(state.get("config_path", str(CONFIG_PATH)))
        config_path.unlink(missing_ok=True)
        try:
            config_path.parent.rmdir()
        except OSError:
            pass
    for directory in state["created_dirs"]:
        try:
            Path(directory).rmdir()
        except OSError:
            pass  # Keep directories that now contain other files.
    print("Uninstalled xpeek. " + (
        "Configuration removed; history preserved."
        if remove_config else "Configuration and history preserved."
    ))


def install() -> None:
    if sys.platform != "linux":
        raise RuntimeError("xpeek currently requires Linux and a Wayland desktop.")
    existing = VENV_DIR.exists() or VENV_DIR.is_symlink()
    if (LINK.exists() or LINK.is_symlink()) and (not existing or not matching_link()):
        raise RuntimeError(f"Refusing to overwrite an existing command: {LINK}")

    state = read_state() if existing else {
        "project": str(PROJECT_DIR), "link": str(LINK), "created_dirs": [],
    }
    config_created = False
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
        config_created = create_config(state)
    except Exception:
        if not existing:
            if MARKER.is_file():
                uninstall(remove_config=config_created)
            elif VENV_DIR.is_dir():
                shutil.rmtree(VENV_DIR)
        raise

    print(f"Installed xpeek: {LINK}")
    print(f"Config {'created' if config_created else 'preserved'}: {CONFIG_PATH}")
    if str(BIN_DIR) not in os.environ.get("PATH", "").split(os.pathsep):
        print('Add ~/.local/bin to your PATH: export PATH="$HOME/.local/bin:$PATH"')
    missing_tools = [name for name in ("grim", "slurp", "wl-copy") if not shutil.which(name)]
    if missing_tools:
        print("Missing system tools: " + ", ".join(missing_tools))
        print("On Arch/CachyOS: sudo pacman -S grim slurp wl-clipboard")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or uninstall xpeek for this user.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="Install xpeek and create config if missing")
    uninstall_parser = commands.add_parser("uninstall", help="Uninstall xpeek; keep config by default")
    config_options = uninstall_parser.add_mutually_exclusive_group()
    config_options.add_argument(
        "--keep-config", dest="remove_config", action="store_false",
        help="Keep user configuration (default)",
    )
    config_options.add_argument(
        "--remove-config", dest="remove_config", action="store_true",
        help="Also remove the user config.json; keep translation history",
    )
    uninstall_parser.set_defaults(remove_config=False)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    try:
        if args.command == "install":
            install()
        else:
            uninstall(remove_config=args.remove_config)
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
