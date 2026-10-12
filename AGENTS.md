# Working on xpeek

`xpeek` is a small Python CLI for copying and translating screen text on Linux/Wayland, primarily developed for gaming; other use cases have not been thoroughly tested. It uses RapidOCR or PaddleOCR tiny/small, GTK4/PyGObject, and gtk4-layer-shell. The command is `xpeek`; keep the README concise and omit a folder-structure listing.

## Development

- Use `.venv/bin/python` for local checks. GTK bindings come from distro packages.
- Use Ruff for Python linting; run `.venv/bin/python -m ruff check .` before committing Python changes.
- `python3 setup.py install` creates or updates the managed environment and command symlink. Rerun it when changing the installed command or package version.
- Preserve existing user settings, saved region, window state, and history during install. The sole config update is refreshing `$schema` to the GitHub schema URL for the installed version's release tag; keep all other fields and config permissions unchanged.
- Do not add automatic configuration or state migrations unless explicitly requested.
- `setup.py update` fetches and verifies the latest stable GitHub release, stops the session's popup process and owned OCR workers, checks out its version tag, and runs that release's installer in a fresh process. Require a managed installation and clean checkout, preserve the selected OCR engines by default, and allow downgrades only with an explicit `--tag`. Stop the popup only after release verification succeeds; no-op or failed updates must not close it. Request clean shutdown, bound the cleanup wait, and target the D-Bus owner's process tree with pidfds if blocked work must be terminated. Confirm process exit before checkout/install; never kill unrelated Python processes.
- `xpeek update` is a thin wrapper around the managed checkout's `setup.py update`, forwarding `--tag` and `--ocr-engines` from any working directory. Keep update logic in setup and avoid loading user config for updates.
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
- After committing and verifying a completed version bump, create an annotated release tag named `vX.Y.Z` at the tested release commit (for example, `git tag -a v0.13.0 -m 'v0.13.0'`). Tag once per completed release batch, after its follow-up changes are committed; never move or replace a published tag.
- When publishing a release, push its tag and use only `vX.Y.Z` as the GitHub release title. Include concise feature notes, installation guidance, and relevant testing limitations.
- Documentation, agent instructions, tests, and internal refactoring without user-visible changes do not require a bump. Honor an explicitly requested version.
- Release `1.0.0` when commands, flags, configuration, and documented behavior are stable; core workflows and installation are reliable on supported systems; and the project is ready to preserve compatibility throughout `1.x`.
- Treat `1.0.0` as a compatibility commitment. It does not require every planned feature, perfect OCR, or a large user base.
- Choose the milestone based on readiness, not the number of `0.x` releases. A direct jump from any `0.x` version is valid.

## Behavior to preserve

- `select` saves a region in `$XDG_STATE_HOME/xpeek/region.json` (default `~/.local/state/xpeek/region.json`); `translate` uses it. Keep selected regions and popup geometry out of config. `ocr` selects a temporary region without changing the saved one and defaults to copying text to the clipboard.
- Provider and language flags override configuration only for that invocation.
- Supported OCR engines are `rapidocr`, `paddleocr-tiny`, and `paddleocr-small`. Setup lets users choose engines; new config defaults follow tiny → RapidOCR → small among those installed. Preserve existing config on updates. `--ocr-engine` overrides it only for the current `translate` or `ocr` invocation, including clipboard mode. Forward each request's engine/options to the popup. Load readers lazily and reuse them while the process lives; own and stop the isolated Paddle worker on process exit. Recognition languages depend on the model, independently of translation language flags.
- OCR defaults to one inference thread, original scale, and color. Per-engine options accept `threads` from 1–4, a finite positive `scale`, and `preprocess` of `none` or `grayscale`. Keep managed Paddle Python, models, and caches inside the installation so uninstall removes them. Do not add a background OCR service without an explicit request.
- Keep the popup process and loaded readers alive for `ocr_keepalive_seconds` after its last window closes and outstanding work finishes (default 60; 0 disables retention). Reopening cancels the idle countdown; closing again restarts it. Visible normal and persistent popups prevent expiry. Expiry exits the process and stops its owned Paddle worker so model memory is returned. Clipboard-only invocations remain independent and do not extend popup retention; do not install a separate background service.
- Capture defaults to desktop per OCR engine. Configure `capture_backend` inside each engine's `ocr_options`; `--capture-backend` overrides only the selected engine for that request. Native gamescope capture is opt-in on supported niri layouts and maps the unchanged logical region into the native frame before OCR preprocessing. Unavailable or uncertain native capture falls back to desktop; warnings go only to the invoking terminal, never popup content or history. Keep capture off the GTK thread and separate from queued OCR work, and do not pass capture settings to OCR readers.
- Translation invocations toggle the single popup in normal mode. Persistent mode appends the latest 10 entries with scrolling; later calls inherit the open popup's mode unless explicitly overridden. Clipboard-only invocations run independently.
- `show-history` displays the latest 10 saved translations by default, accepts a positive `-n` / `--limit` override, and starts at the bottom in chronological order. Its limit does not change persistent mode's 10-entry limit. Persistent appending follows new entries only when already at the bottom; scrolling up preserves the reading position.
- Persistent and normal popup heights are remembered separately. Provider, language, and persistence flags must not write configuration, and appending must preserve saved placement and capture each request before queued translation work.
- Use layer-shell for independent popup placement on niri. Preserve remembered geometry, dragging between monitors, and proportional placement on disconnect.
- Keep explicit layer-shell output selection authoritative during remapping; GDK's surface output can still be stale at `map`. Drops may span connected monitors, with interactive portions sharing the same text and scroll position.
- Automatic monitor fallback must preserve the saved home and position, including across restarts and reconnections. Only dragging the header saves a new monitor and position; resizing updates remembered size.
- Without layer-shell, let the compositor handle normal GTK window placement.
