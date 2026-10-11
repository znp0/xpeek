# Gaming OCR benchmarks

Measured on October 10–11, 2026, on an Intel Core i7-12700H with Linux/CachyOS. All three engines used CPU inference with one inference/OpenCV/OpenMP/BLAS thread; workers were pinned to logical CPU 0 with effective nice 6. Testing covered only Wuthering Waves, its font, and English dialogue; other games are untested. These static-image results do not measure screenshot capture, translation network time, popup latency, game FPS, or performance on other machines.

RapidOCR 1.2.3 used ONNX Runtime 1.30.0 and its bundled PP-OCRv3 models. PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 / PaddleX 3.7.2 used matching PP-OCRv6 tiny/small detector and recognizer pairs, MKLDNN disabled, and orientation/document preprocessing disabled. RapidOCR's angle classifier remained enabled. All rounds verified matching model hashes. The [PaddleOCR documentation](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html) describes these models and pipeline settings.

## Full desktop region and preprocessing

25 captures used the same 2270 × 1060 saved desktop region from a game rendered at 1280 × 800 and displayed at 2560 × 1600 through gamescope. Coverage included ordinary dialogue, small cinematic subtitles, orange text, choices, a dense log, and scenes without dialogue. Accuracy averages the 23 text-bearing images; latency is the warm median over 21 ordinary dialogue images with five repetitions each. CER is character error rate, WER is word error rate; lower is better. Speaker names are secondary and excluded where separable.

| Engine / input | Warm median | CER | WER | Dialogue meaning |
| --- | ---: | ---: | ---: | --- |
| RapidOCR / original | 0.976 s | 6.24% | 16.14% | Lost substantial orange dialogue and a condition; some merged words changed meaning |
| Paddle tiny / original | 1.142 s | 1.57% | 9.19% | Preserved difficult clauses; occasional term and word-boundary errors |
| Paddle small / original | 2.716 s | 0.59% | 2.85% | Preserved all annotated clauses and choices with cleaner wording |
| Paddle tiny / half size | 0.425 s | 5.31% | 12.79% | Lost an entire cinematic sentence; unsuitable as a blanket default |
| Paddle small / half size | 1.172 s | 0.33% | 2.39% | Retained all annotated clauses and choices on this set |
| Paddle tiny / manual layout crop | 0.333 s | 1.67% | 9.19% | Retained difficult clauses; residual spelling/term errors |
| Paddle small / manual layout crop | 0.973 s | 0.59% | 2.13% | Retained clauses with cleaner text, at roughly baseline RapidOCR latency |

Half-size inputs used OpenCV area interpolation. Manual cropping used one of six hand-assigned UI-layout presets per image, not an automatic dialogue finder or one universal saved region. Cropping requires layout knowledge and is not implemented as an xpeek preset feature. RapidOCR's short-input enlargement and wide-image shortcut can make a smaller region slower or less accurate.

A RapidOCR detector cap of 1536 reduced the median to 0.571 s and CER to 3.02%, but still lost the Sentinel condition and misread the recipient. A 1024 cap reached 0.405 s but reduced one entire cinematic sentence to `many times.`; neither is a safe universal correction. Detector caps are not exposed by the current config.

## Native gamescope frames

The separate native round used 18 unscaled 1280 × 800 PNGs, including 14 dialogue/choice scenes, a dense log, and three scenes without dialogue. These are different scenes and workloads from the desktop set. Latency pools 70 warm calls across the 14 dialogue/choice images; accuracy is the per-image macro average. There were 84 engine/input cases overall, 504 measured calls, and 420 warm observations. Each case's text, boxes, and confidence were identical across repeats. Native orange dialogue was unavailable.

| Engine | Warm median | CER | WER | Dialogue meaning |
| --- | ---: | ---: | ---: | --- |
| RapidOCR | 0.609 s | 4.23% | 11.54% | Dropped the opening line of the titles explanation; also missed a whole response in the separate log |
| Paddle tiny | 0.677 s | 1.52% | 9.36% | Retained all dialogue clauses; still fused some choice words |
| Paddle small | 1.825 s | 0.61% | 6.19% | Retained all clauses with cleaner text; mild fused-word/icon issues remained |

Tiny was the strongest full-native speed/meaning compromise. Small gave the cleanest text, while RapidOCR was slightly faster but missed content. Dense-log times were 1.764 / 1.446 / 5.741 s for RapidOCR / tiny / small respectively. All engines could recognize unrelated HUD text; Paddle sometimes interpreted artwork as characters on a scene without dialogue. Full-image OCR is not a dialogue filter.

## Matching native, displayed, and reduced regions

Three paired scenes had the same dialogue/choices. Native pixels were cropped directly to 1135 × 530 at `(39, 257)`; native images were not resized for this comparison. Corresponding desktop regions were 2270 × 1060, and downscaled desktop inputs were 1135 × 530. Image registration had median error below 0.4 native pixels, but captures were sequential with animated backgrounds, so these are not pixel-exact simultaneous pairs. Each table value pools 15 warm calls.

| Engine | Native crop | Displayed region | Displayed region → half size |
| --- | ---: | ---: | ---: |
| RapidOCR | 0.640 s | 1.044 s | 0.579 s |
| Paddle tiny | 0.432 s | 1.227 s | 0.406 s |
| Paddle small | 1.365 s | 2.557 s | 1.051 s |

| Engine | Meaning across the paired scenes |
| --- | --- |
| RapidOCR | Native crop recovered praise lost from the full native frame. Downscaling damaged word boundaries; the word `help` became an untranslated fused fragment. Choices remained troublesome. |
| Paddle tiny | Native crop damaged `help` despite the correct full-frame result; downscaling damaged a term in the praise sentence. `who I am` was fused into `wholam` in choices across variants. |
| Paddle small | Core meaning survived all paired versions. Native crops had more icon/fused-word noise; displayed half-size results were cleaner on these scenes. |

The native PNGs were opaque RGBA and desktop PNGs RGB. RapidOCR's installed loader swaps red/blue on RGBA but preserves RGB on RGB files; Paddle consistently uses BGR. A RapidOCR RGB-native control measured 0.547 s with CER 4.61%, versus 0.640 s / 4.21% for the normal native crop. This diagnostic prevents attributing every difference solely to resolution.

## Startup and grayscale

These startup observations used fresh Python/reader processes with the normal OS file cache, not cache-flushed machine startup. Launch-to-ready includes import and reader initialization. First OCR is separate; neither column is warm inference or capture-to-popup latency.

| Engine | Launch → reader ready | First native OCR |
| --- | ---: | ---: |
| RapidOCR | 0.980 s | 0.738 s |
| Paddle tiny | 1.392 s | 0.753 s |
| Paddle small | 1.580 s | 1.982 s |

RapidOCR's original measurements completed before its final metadata lookup failed; a separate audit reader recovered the first-OCR/equivalence metadata without rerunning the measured batch. The implementation's owned worker and GTK process add their own overhead; these figures describe benchmark readers, not an end-to-end application promise.

A separate 25-image desktop grayscale round found RapidOCR CER improved from 6.24% to 3.21%, while WER worsened from 16.14% to 23.04% because words merged. Tiny changed from 1.57% to 1.91% CER, and small from 0.59% to 0.56%. Some tiny grayscale outputs changed a direction or turned `I need` into a named subject. That round included 0.15–0.17 s of temporary PNG preprocessing, unlike the application's in-memory path. Native grayscale was not tested.

## How to use these results

Start with tiny, color, original resolution, and one thread. Choose small when cleaner dialogue is worth the extra processing time; choose RapidOCR for its lightweight runtime. If your game is heavily upscaled, small at `scale: 0.5` is worth a local comparison, but tiny's cinematic omission demonstrates why reduced resolution cannot be the general default. Crops, grayscale, and native capture all depend on the font, layout, and capture path.

Character scores are supporting evidence: a low CER can hide a missing condition, merged phrase, or important game term. Meaning review used joined dialogue and Google English → Vietnamese controls, checking omissions, negations, subjects, conditions, terms, and choice order. Provider mistranslations still occur with perfect OCR. The samples and detailed research notes remain local and Git-ignored; this page publishes the curated findings rather than linking unavailable files.
