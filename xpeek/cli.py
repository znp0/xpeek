"""Command-line interface.

Commands:
    xpeek select          -- interactively pick and save a screen region
    xpeek translate       -- capture saved region -> OCR -> translate -> print
    xpeek ocr             -- select temporary region -> OCR -> clipboard
    xpeek history         -- show translation history
    xpeek clear-history   -- wipe translation history
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from .capture import CaptureError, capture_region, select_region
from .clipboard import ClipboardError, copy_text
from .config import Config, ConfigError, CONFIG_PATH, Region
from .history import HistoryStore
from .notifications import notify_clipboard_copied
from .ocr import OcrError, extract_text
from .providers import PROVIDERS


def _source_language(value: str) -> str:
    return "auto" if value.lower() in ("auto", "detect") else value


def cmd_select(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        region = select_region()
    except CaptureError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    config.region = region
    config.save()
    print(f"Saved region {region.as_geometry()} to {CONFIG_PATH}")
    return 0


def _send_signal(sig: int, pid_file: Path | None = None) -> bool:
    if pid_file is None:
        pid_file = Path("/tmp/xpeek.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, sig)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            pid_file.unlink(missing_ok=True)
    return False

def cmd_translate(args: argparse.Namespace) -> int:
    # Every provider shares one window. An existing instance only closes;
    # apply the requested provider when starting the next instance.
    pid_file = Path("/tmp/xpeek.pid")
    if _send_signal(signal.SIGUSR1, pid_file):
        return 0

    config = Config.load()

    if config.region is None:
        print(
            "No region has been saved yet. Run `xpeek select` first.",
            file=sys.stderr,
        )
        return 1

    if args.provider == "clipboard":
        return _ocr_to_clipboard(config, config.region)

    if args.provider is not None:
        config.provider = args.provider
    if args.source_lang is not None:
        config.source_lang = args.source_lang
    config.source_lang = _source_language(config.source_lang)
    if args.target_lang is not None:
        config.target_lang = args.target_lang

    pid_file.write_text(str(os.getpid()))
    try:
        from .gui import run_gui_translation
        return run_gui_translation(config, mode="translate")
    except ImportError as exc:
        print(f"Error loading GUI: {exc}", file=sys.stderr)
        print("Please ensure PySide6 is installed: pip install PySide6", file=sys.stderr)
        return 1
    finally:
        pid_file.unlink(missing_ok=True)


def cmd_ocr(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        region = select_region()
    except CaptureError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return _ocr_to_clipboard(config, region)


def _ocr_to_clipboard(config: Config, region: Region) -> int:
    try:
        image_path = capture_region(region)
        try:
            text = extract_text(image_path)
        finally:
            image_path.unlink(missing_ok=True)
        if not text.strip():
            print("No text detected; clipboard was left unchanged.", file=sys.stderr)
            return 1
        copy_text(text)
    except (CaptureError, OcrError, ClipboardError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("OCR text copied to clipboard.")
    notify_clipboard_copied()
    return 0


def cmd_show_last(args: argparse.Namespace) -> int:
    if _send_signal(signal.SIGUSR2):
        return 0

    config = Config.load()
    Path("/tmp/xpeek.pid").write_text(str(os.getpid()))
    try:
        from .gui import run_gui_translation
        res = run_gui_translation(config, mode="last")
        Path("/tmp/xpeek.pid").unlink(missing_ok=True)
        return res
    except ImportError as exc:
        print(f"Error loading GUI: {exc}", file=sys.stderr)
        Path("/tmp/xpeek.pid").unlink(missing_ok=True)
        return 1


def cmd_history(args: argparse.Namespace) -> int:
    config = Config.load()
    history = HistoryStore(limit=config.history_limit)
    entries = history.all()

    if not entries:
        print("No history yet.")
        return 0

    if args.limit:
        entries = entries[-args.limit :]

    for entry in entries:
        print(f"[{entry.timestamp}]")
        print(f"  OCR: {entry.ocr_text}")
        print(f"  Translation: {entry.translation}")
        print()
    return 0


def cmd_clear_history(args: argparse.Namespace) -> int:
    config = Config.load()
    history = HistoryStore(limit=config.history_limit)
    history.clear()
    print("History cleared.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    provider_choices = sorted([*PROVIDERS, "clipboard"])
    parser = argparse.ArgumentParser(
        prog="xpeek",
        description="Copy and translate screen text on Wayland.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "translate options (place after translate):\n"
            "  -p, --provider {" + ",".join(provider_choices) + "}\n"
            "                        Override the configured provider without saving;\n"
            "                        clipboard copies OCR text without translation.\n"
            "  -s, --source-lang CODE Source language; auto/detect requests detection.\n"
            "  -t, --target-lang CODE Target language for translation (configured default).\n\n"
            "Examples:\n"
            "  xpeek translate --provider ollama\n"
            "  xpeek translate -p gemini\n"
            "  xpeek translate --source-lang en --target-lang ja\n"
            "  xpeek translate -s auto -t vi\n"
            "  xpeek translate --provider=clipboard\n"
            "  xpeek ocr\n\n"
            "Use translate --help for provider options and window behavior."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_select = subparsers.add_parser("select", help="Select and save the OCR region")
    p_select.set_defaults(func=cmd_select)

    p_translate = subparsers.add_parser(
        "translate", help="Capture saved region, OCR, and translate or copy",
        description=(
            "If a window already exists, close it and exit regardless of provider.\n"
            "Otherwise capture the saved region, run OCR, and translate it with\n"
            "the selected provider. With clipboard, copy the OCR text without\n"
            "translation or opening a window.\n\n"
            "Language codes depend on the provider. Language flags affect translation\n"
            "only; they do not change the bundled OCR recognition model."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xpeek translate                 # configured default\n"
            "  xpeek translate --provider ollama\n"
            "  xpeek translate -p gemini\n"
            "  xpeek translate -s en -t ja\n"
            "  xpeek translate -s auto -t vi\n"
            "  xpeek translate --provider=clipboard\n\n"
            "Use xpeek ocr to select a temporary region and copy its text."
        ),
    )
    p_translate.add_argument(
        "-p", "--provider", choices=provider_choices, default=None,
        help=(
            "Override the configured provider without saving; "
            "clipboard copies OCR text without translation"
        ),
    )
    p_translate.add_argument(
        "-s", "--source-lang", metavar="CODE", type=_source_language, default=None,
        help=(
            "Source language, e.g. en, or auto/detect for automatic detection; "
            "defaults to config (initially auto)"
        ),
    )
    p_translate.add_argument(
        "-t", "--target-lang", metavar="CODE", default=None,
        help="Target language for translation, e.g. ja; defaults to config (initially en)",
    )
    p_translate.set_defaults(func=cmd_translate)

    p_ocr = subparsers.add_parser(
        "ocr", help="Select a temporary region, OCR it, and copy to clipboard",
        description=(
            "Interactively select a temporary region and copy its OCR text to the Wayland "
            "clipboard using wl-copy (install wl-clipboard). Preserves line "
            "breaks without changing the saved region or toggling a translation "
            "window. Use translate -p=clipboard to copy text from the saved region."
        ),
    )
    p_ocr.set_defaults(func=cmd_ocr)

    p_show_last = subparsers.add_parser(
        "show-last", help="Show the last translation result"
    )
    p_show_last.set_defaults(func=cmd_show_last)

    p_history = subparsers.add_parser("history", help="Show translation history")
    p_history.add_argument(
        "-n", "--limit", type=int, default=None, help="Only show the last N entries"
    )
    p_history.set_defaults(func=cmd_history)

    p_clear = subparsers.add_parser("clear-history", help="Clear translation history")
    p_clear.set_defaults(func=cmd_clear_history)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
