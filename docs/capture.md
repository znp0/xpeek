# Capture

Desktop capture is the default: `grim` captures the selected region after gamescope and desktop scaling. Native gamescope capture instead takes the game frame before upscaling and maps the same region into it. `select` still saves desktop logical coordinates; `ocr` still uses a temporary selection.

## Enable native capture

Requires niri with window layout IPC, Linux `/proc`, and `gamescopectl` from the gamescope package. Launch gamescope with explicit native dimensions and fullscreen or borderless output, for example:

```bash
gamescope -w 1280 -h 800 -W 2560 -H 1600 -f -- YOUR_GAME
xpeek translate --capture-backend gamescope
xpeek ocr --capture-backend gamescope
xpeek translate -p clipboard --capture-backend gamescope
```

For an engine default, set `"capture_backend": "gamescope"` inside `ocr_options.ENGINE`. Switching engines selects that engine's capture setting; `--capture-backend desktop` overrides it for one invocation. Existing configurations remain unchanged during setup, and engines default to desktop when this setting is absent. Keep capture settings out of the top-level config.

```json
{
  "ocr_engine": "paddleocr-tiny",
  "ocr_options": {
    "paddleocr-tiny": {"capture_backend": "gamescope", "scale": 1},
    "paddleocr-small": {"capture_backend": "desktop", "scale": 0.5}
  }
}
```

The window is selected by the region, not its title or focus. Its gamescope process must own a single identifiable `gamescope-*` Wayland socket. `GAMESCOPE_WAYLAND_DISPLAY` can resolve multiple sockets, but must belong to the selected game; setting it is normally unnecessary.

## Supported layouts and fallback

Initial support covers visible fullscreen/borderless gamescope windows on niri, with matching native/window aspect ratios, standard `auto` or `fit` scaling, and unrotated outputs. The whole selected region must fit inside the game window, which must fit on one output. Fractional desktop scale and nonzero or negative monitor origins are handled. Niri reports positions for floating windows; for tiled windows, only an active output-sized game launched with `-f` can be mapped from the output origin. Other tiled layouts fall back.

Decorated windows, letterboxing, integer/fill/stretch scaling, custom maximum scales, niri overview, ambiguous sockets, and uncertain or changing geometry use desktop capture instead. Avoid changing gamescope zoom or the game's internal render dimensions during a capture; these cannot be established from the launch dimensions alone. Other compositors also fall back to desktop.

Native capture has a two-second deadline; desktop capture has a three-second command timeout. When native capture is unavailable, xpeek prints a warning only to the invoking terminal's stderr and continues. There is no popup warning or extra notification, and warnings are not saved in history. Shortcuts without a terminal may send stderr to the desktop's logs. If desktop capture also fails, the command reports an error.

Trigger capture while the game is focused. Some games pause or produce blank native frames when unfocused, even when the desktop still shows their last frame; a valid blank PNG cannot be distinguished reliably from a blank scene.

## OCR settings

All three OCR engines work with either backend. Cropping happens before OCR preprocessing, with no capture-layer resizing. Start native capture with one thread, `scale: 1`, and `preprocess: "none"`; `scale: 0.5` would halve the native crop again. Desktop half-scale recommendations do not automatically apply to native images.

Existing native-image experiments covered Wuthering Waves on one machine, not other games. See the [benchmark results](benchmarks.md) for the tested resolution and meaning-retention comparisons. Native capture removes the desktop upscaling stage; it does not guarantee better OCR for every font or scene.
