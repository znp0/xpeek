"""In-memory preprocessing shared by OCR backends."""
from __future__ import annotations

import math


def preprocess_image(image, scale: float, preprocess: str, *, color_order: str):
    import cv2

    height, width = image.shape[:2]
    if scale != 1:
        dimensions = (width * scale, height * scale)
        if not all(math.isfinite(value) and value >= 0.5 for value in dimensions):
            raise ValueError("OCR scale produces invalid or zero-sized image dimensions")
        size = tuple(round(value) for value in dimensions)
        if not all(size):
            raise ValueError("OCR scale produces zero-sized image dimensions")
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    if preprocess == "grayscale" and image.ndim == 3:
        conversion = cv2.COLOR_RGB2GRAY if color_order == "RGB" else cv2.COLOR_BGR2GRAY
        image = cv2.cvtColor(image, conversion)
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image
