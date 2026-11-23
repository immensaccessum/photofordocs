"""Снимок на стойке: исходник, рамка, тон и силуэт."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class Shot:

    path: Path
    image: Image.Image
    working: Image.Image
    face: object
    zoom: float = 1.0
    shift_x: float = 0.0
    shift_y: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    gamma: float = 0.0
    saturation: float = 0.0
    sharpness: float = 0.0
    black_point: float = 0.0
    white_point: float = 255.0
    tolerance: float = 42.0
    replace: bool = False
    angle: float = 0.0
    eye_t: float = 0.55
    guides_manual: bool = False
    # Линия глаз, которую оператор оставил на этом снимке. Двухточечный формат
    # её не показывает, но при возврате к трёхточечному она должна встать туда же.
    eye_line: float = 0.55
    crown: tuple[float, float] | None = None
    chin: tuple[float, float] | None = None
    matte: np.ndarray | None = None
    matte_of: int = 0
