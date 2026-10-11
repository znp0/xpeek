# OCR engines

Run `python3 setup.py install` to choose one or more engines. Enter comma-separated numbers or names, or `all` for all three; Enter accepts the displayed selection (tiny on fresh installs, already-installed engines on updates). Non-interactive installs require `--ocr-engines`; missing or empty values fail. Unselected engines are removed from this managed installation; existing config settings are preserved while `$schema` is refreshed, so adjust `ocr_engine` if you remove its engine.

```bash
python3 setup.py install --ocr-engines rapidocr,paddleocr-tiny,paddleocr-small
python3 setup.py install --ocr-engines all
xpeek translate --ocr-engine paddleocr-tiny
xpeek ocr --ocr-engine paddleocr-small
xpeek translate --ocr-engine rapidocr -p clipboard
```

| Engine | Implementation | Tradeoff observed in our tests |
| --- | --- | --- |
| `rapidocr` | RapidOCR 1.2.3, bundled PP-OCRv3 ONNX models | Lightweight; some missing dialogue and merged words |
| `paddleocr-tiny` | PP-OCRv6 tiny detector + recognizer | Good speed/dialogue-retention balance |
| `paddleocr-small` | PP-OCRv6 small detector + recognizer | Cleaner dialogue, slower inference |

Remove selected engines without rebuilding or downloading dependencies:

```bash
python3 setup.py remove-engines paddleocr-small
python3 setup.py remove-engines rapidocr,paddleocr-small
```

At least one engine must remain; use `uninstall` to remove everything. This removes the selected engine packages/model files, and removes the isolated Paddle runtime/cache when no Paddle engine remains. Shared dependencies needed by remaining engines stay installed. Config, saved region, popup state, and history stay unchanged; update `ocr_engine` if it points to a removed engine. Rerun install to add engines back.

Paddle uses managed CPython 3.12.15 with PaddleOCR 3.7.0, PaddlePaddle 3.3.1, and PaddleX 3.7.2. It runs independently of distro GTK bindings through an application-owned subprocess. Managed Paddle supports glibc Linux on x86_64; other platforms can select RapidOCR subject to its wheel availability. Downloads, models, and caches stay inside `.venv` and are removed by uninstall.

Readers load on first use and stay loaded while the current application process lives. A persistent popup benefits from warm models; clipboard commands and closing/reopening a normal popup start new processes. There is no background OCR service or warmup command yet. Benchmark warm timings exclude model startup, capture, and translation.

## Options

Set options by engine in config; omitted options use their defaults. For example, to experiment with half-resolution Paddle small:

```json
{
  "ocr_engine": "paddleocr-small",
  "ocr_options": {
    "paddleocr-small": {"threads": 1, "scale": 0.5, "preprocess": "none"}
  }
}
```

| Option | Default | Accepted values |
| --- | --- | --- |
| `capture_backend` | `"desktop"` | `desktop` or `gamescope`; selected per engine, with a temporary `--capture-backend` override |
| `threads` | `1` | Integer 1–4; controls inference/library parallelism, not the total count of idle OS threads |
| `scale` | `1` | Finite positive factor; 1 preserves captured resolution, 0.5 halves each dimension |
| `preprocess` | `"none"` | `"none"` keeps color; `"grayscale"` removes color |

Resizing happens in memory before OCR, using area interpolation when reducing and cubic interpolation when enlarging. Grayscale follows resizing. Invalid settings and scales yielding zero-sized images report errors. Internal detector/recognizer resizing still occurs; a smaller input does not guarantee faster processing, particularly for RapidOCR.

Keep one thread for a modest CPU footprint. Our thread experiments found limited gains for these candidates, and one-thread measurements are the basis of the [benchmark summary](benchmarks.md). This is not an operating-system CPU quota.

Start with original color/resolution. Paddle small at 0.5× preserved dialogue well on the tested upscaled desktop images, while tiny at 0.5× lost an entire cinematic sentence. Grayscale sometimes improved character scores but damaged word boundaries or meaning; it has not been tested on the native dataset. These are optional settings, not universal recommendations.

Translation's source/target flags do not select OCR language models. Recognition languages depend on each bundled model. Our tests focus on English gaming dialogue; other scripts and uses are not validated. Tesseract is no longer supported. Remove obsolete Tesseract settings manually if present; setup does not migrate config.

Capture defaults to `grim` on the selected desktop region. Optional [native gamescope capture](capture.md) maps that region into the game frame on supported niri layouts. OCR scaling and grayscale apply afterward. Automatic dialogue filtering, region presets, and speed/accuracy modes are not implemented.
