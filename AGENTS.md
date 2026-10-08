# Working on xpeek

`xpeek` is a small Python CLI for copying and translating screen text on Linux/Wayland. It uses RapidOCR, GTK4/PyGObject, and gtk4-layer-shell. The command is `xpeek`; keep the README concise and omit a folder-structure listing.

## Development

- Use `.venv/bin/python` for local checks. GTK bindings come from distro packages.
- `python3 setup.py install` creates or updates the managed environment and command symlink. Rerun it when changing the installed command or package version.
- Preserve existing user configuration, window state, and history during install.
- Run checks relevant to the change and `git diff --check` before committing. GUI input changes need real pointer testing when possible; simulated coordinate tests alone can miss compositor behavior. Report live-testing limitations.
- Keep task-specific test scripts and build artifacts out of the repository; use temporary files and remove them when finished.
- Never read out or commit API keys, `.env`, or user configuration containing secrets. `.env.example` must contain placeholders only.
- Use Conventional Commits, such as `feat(gui): ...`, `fix(cli): ...`, and `docs: ...`. Keep documentation and CLI help aligned with behavior changes.
- Do not hard-wrap Markdown prose. Keep each paragraph and list item on one source line and let the viewer wrap it visually. Preserve intentional line breaks in code blocks, tables, and other Markdown structure.

## Versioning

- Update the version as work is completed, before committing any new feature or user-visible bug fix. Do not leave version updates until a later release task.
- Keep `[project].version` in `pyproject.toml` and `__version__` in `xpeek/__init__.py` identical, and verify installed package metadata after rebuilding.
- Increment the minor version for a feature and reset the patch to zero (`0.6.2` -> `0.7.0`). Increment the patch for a bug fix (`0.6.0` -> `0.6.1`).
- While the project is below `1.0.0`, breaking changes also increment the minor version. From `1.0.0` onward, breaking changes increment the major version.
- Bump once per completed change batch, using its highest-impact change; do not bump again for follow-up commits within that same batch.
- Documentation, agent instructions, tests, and internal refactoring without user-visible changes do not require a bump. Honor an explicitly requested version.

## Behavior to preserve

- `select` saves a region; `translate` uses it. `ocr` selects a temporary region without changing the saved one and defaults to copying text to the clipboard.
- Provider and language flags override configuration only for that invocation.
- Translation invocations toggle the single popup in normal mode. Persistent mode appends the latest 10 entries with scrolling; later calls inherit the open popup's mode unless explicitly overridden. Clipboard-only invocations run independently.
- `show-history` displays the latest 10 saved translations by default, accepts a positive `-n` / `--limit` override, and starts at the bottom in chronological order. Its limit does not change persistent mode's 10-entry limit. Persistent appending follows new entries only when already at the bottom; scrolling up preserves the reading position.
- Persistent and normal popup heights are remembered separately. Provider, language, and persistence flags must not write configuration, and appending must preserve saved placement and capture each request before queued translation work.
- Use layer-shell for independent popup placement on niri. Preserve remembered geometry, dragging between monitors, and proportional placement on disconnect.
- Keep explicit layer-shell output selection authoritative during remapping; GDK's surface output can still be stale at `map`. Drops may span connected monitors, with interactive portions sharing the same text and scroll position.
- Automatic monitor fallback must preserve the saved home and position, including across restarts and reconnections. Only dragging the header saves a new monitor and position; resizing updates remembered size.
- Without layer-shell, let the compositor handle normal GTK window placement.
