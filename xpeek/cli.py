"""Command-line interface.

Commands:
    xpeek select          -- interactively pick and save a screen region
    xpeek translate       -- capture saved region -> OCR -> translate -> print
    xpeek ocr             -- select temporary region -> OCR -> copy or translate
    xpeek show-history    -- show latest 10 translations in a popup
    xpeek history         -- print translation history in the terminal
    xpeek clear-history   -- wipe translation history
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .capture import CaptureError, capture_region, select_region
from .clipboard import ClipboardError, copy_text
from .config import Config, ConfigError, CONFIG_PATH, Region
from .history import HistoryStore
from .notifications import notify_clipboard_copied
from .ocr import OcrError, extract_text
from .providers import PROVIDERS


def _source_language(value: str) -> str:
    return "auto" if value.lower() in ("auto", "detect") else value


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


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


def cmd_translate(args: argparse.Namespace) -> int:
    config = Config.load()
    if (args.provider or config.provider) == "clipboard":
        if config.region is None:
            print("No region has been saved yet. Run `xpeek select` first.", file=sys.stderr)
            return 1
        return _ocr_to_clipboard(config, config.region)

    return _translate_region(config, config.region, args)


def _translate_region(
    config: Config, region: Region | None, args: argparse.Namespace, *, temporary: bool = False
) -> int:
    config = replace(
        config,
        region=region,
        provider=args.provider if args.provider is not None else config.provider,
        source_lang=_source_language(
            args.source_lang if args.source_lang is not None else config.source_lang
        ),
        target_lang=args.target_lang if args.target_lang is not None else config.target_lang,
    )
    try:
        from .gui import run_gui_translation
        return run_gui_translation(
            config, mode="translate", persistent=args.persistent, temporary=temporary
        )
    except ImportError as exc:
        print(f"Error loading GUI: {exc}", file=sys.stderr)
        print("Install GTK4 and python-gobject, then rerun python3 setup.py install.", file=sys.stderr)
        return 1


def cmd_ocr(args: argparse.Namespace) -> int:
    config = Config.load()
    if args.provider != "clipboard":
        return _translate_region(config, None, args, temporary=True)
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


def cmd_show_history(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        from .gui import run_gui_translation
        return run_gui_translation(config, mode="last", display_limit=args.limit)
    except ImportError as exc:
        print(f"Error loading GUI: {exc}", file=sys.stderr)
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

    def add_translation_options(
        command_parser: argparse.ArgumentParser, provider_default: str | None, provider_help: str
    ) -> None:
        command_parser.add_argument(
            "-p", "--provider", choices=provider_choices, default=provider_default,
            help=provider_help,
        )
        command_parser.add_argument(
            "-s", "--source-lang", metavar="CODE", type=_source_language, default=None,
            help=(
                "Source language, e.g. en, or auto/detect for automatic detection; "
                "defaults to config (initially auto)"
            ),
        )
        command_parser.add_argument(
            "-t", "--target-lang", metavar="CODE", default=None,
            help="Target language for translation, e.g. ja; defaults to config (initially en)",
        )
        command_parser.add_argument(
            "--persistent", action=argparse.BooleanOptionalAction, default=None,
            help=("Append to a taller popup keeping the latest 10 translations; "
                  "--no-persistent restores close-on-invocation behavior. "
                  "Defaults to the open popup's mode, or config when opening one"),
        )

    parser = argparse.ArgumentParser(
        prog="xpeek",
        description="Copy and translate screen text on Wayland.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "translate / ocr options (place after the command):\n"
            "  -p, --provider {" + ",".join(provider_choices) + "}\n"
            "                        Provider (translate: config; ocr: clipboard);\n"
            "                        clipboard copies OCR text without translation.\n"
            "  -s, --source-lang CODE Source language; auto/detect requests detection.\n"
            "  -t, --target-lang CODE Target language for translation (configured default).\n\n"
            "  --persistent          Keep the popup open and append translations (latest 10).\n"
            "  --no-persistent       Use the default close-on-invocation behavior.\n\n"
            "Examples:\n"
            "  xpeek translate --provider ollama\n"
            "  xpeek translate -p gemini\n"
            "  xpeek translate --persistent\n"
            "  xpeek translate --source-lang en --target-lang ja\n"
            "  xpeek translate -s auto -t vi\n"
            "  xpeek translate --provider=clipboard\n"
            "  xpeek ocr\n"
            "  xpeek ocr -p gemini -s auto -t en\n\n"
            "Use translate --help or ocr --help for window behavior and examples."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_select = subparsers.add_parser("select", help="Select and save the OCR region")
    p_select.set_defaults(func=cmd_select)

    p_translate = subparsers.add_parser(
        "translate", help="Capture saved region, OCR, and translate or copy",
        description=(
            "Capture the saved region, run OCR, and translate with the selected provider.\n"
            "By default, an existing popup closes and the invocation exits.\n"
            "With --persistent, append results to a taller popup keeping the latest 10.\n"
            "Further calls reuse its mode; --no-persistent closes an existing popup.\n"
            "Clipboard mode copies the OCR text and runs independently.\n\n"
            "Language codes depend on the provider. Language flags affect translation\n"
            "only; they do not change the bundled OCR recognition model."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xpeek translate                 # configured default\n"
            "  xpeek translate --persistent    # append mode\n"
            "  xpeek translate --no-persistent # close an existing popup\n"
            "  xpeek translate --provider ollama\n"
            "  xpeek translate -p gemini\n"
            "  xpeek translate -s en -t ja\n"
            "  xpeek translate -s auto -t vi\n"
            "  xpeek translate --provider=clipboard\n\n"
            "Use xpeek ocr -p PROVIDER to translate a temporary region."
        ),
    )
    add_translation_options(
        p_translate, None,
        (
            "Override the configured provider without saving; "
            "clipboard copies OCR text without translation"
        ),
    )
    p_translate.set_defaults(func=cmd_translate)

    p_ocr = subparsers.add_parser(
        "ocr", help="Select a temporary region, OCR it, and copy or translate",
        description=(
            "Select a temporary region without changing the saved region.\n"
            "Default: copy its OCR text to the clipboard using wl-copy.\n"
            "With a translation provider, show the translation in an overlay.\n"
            "If a window already exists, a translation request closes it and exits\n"
            "without selecting a region, unless the popup is persistent.\n"
            "--persistent appends to a taller popup keeping the latest 10 translations.\n"
            "Further calls reuse its mode; --no-persistent restores close behavior.\n"
            "Clipboard mode runs independently and ignores persistence flags.\n\n"
            "Language flags affect translation only, not OCR recognition."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xpeek ocr                       # temporary region -> clipboard\n"
            "  xpeek ocr -p google             # temporary region -> translation\n"
            "  xpeek ocr -p google --persistent # append a temporary-region translation\n"
            "  xpeek ocr -p gemini -s auto -t en\n\n"
            "Use translate -p=clipboard to copy text from the saved region."
        ),
    )
    add_translation_options(
        p_ocr, "clipboard",
        "Translation provider; defaults to clipboard (copy original OCR text)",
    )
    p_ocr.set_defaults(func=cmd_ocr)

    p_show_history = subparsers.add_parser(
        "show-history", help="Show recent translations in a popup (default: latest 10)",
        description=("Show recent saved translations, oldest first, scrolled to the bottom. "
                     "If a popup already exists, close it and exit."),
    )
    p_show_history.add_argument(
        "-n", "--limit", type=_positive_int, default=10, metavar="N",
        help="Show the latest N saved translations; positive integer (default: 10)",
    )
    p_show_history.set_defaults(func=cmd_show_history)

    p_history = subparsers.add_parser("history", help="Print translation history in the terminal")
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
