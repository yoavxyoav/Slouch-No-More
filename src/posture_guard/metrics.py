"""Feature extraction from MediaPipe pose landmarks.

All coordinates are normalized image coordinates (0..1, y grows downward),
so features are resolution-independent. The feature vector is what
calibration and classification operate on.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence

# MediaPipe PoseLandmarker indices
NOSE = 0
LEFT_EYE = 2
RIGHT_EYE = 5
LEFT_EAR = 7
RIGHT_EAR = 8
MOUTH_LEFT = 9
MOUTH_RIGHT = 10
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12

# Degrees are scaled down so tilt lives on a comparable scale to the
# normalized-coordinate features (~0..1).
TILT_SCALE = 100.0

FEATURE_NAMES = [
    "nose_x",
    "nose_y",
    "shoulder_mid_y",
    "shoulder_width",
    "head_drop",
    "shoulder_tilt",
]


@dataclass(frozen=True)
class PostureMetrics:
    nose_x: float
    nose_y: float
    shoulder_mid_y: float
    shoulder_width: float
    head_drop: float  # nose_y - shoulder_mid_y; grows as the head sinks
    shoulder_tilt_deg: float

    def to_vector(self) -> list[float]:
        return [
            self.nose_x,
            self.nose_y,
            self.shoulder_mid_y,
            self.shoulder_width,
            self.head_drop,
            self.shoulder_tilt_deg / TILT_SCALE,
        ]


def median_metrics(samples: Sequence[PostureMetrics]) -> PostureMetrics | None:
    """Per-feature median of several readings - smooths single-frame noise."""
    if not samples:
        return None
    med = statistics.median
    return PostureMetrics(
        nose_x=med(s.nose_x for s in samples),
        nose_y=med(s.nose_y for s in samples),
        shoulder_mid_y=med(s.shoulder_mid_y for s in samples),
        shoulder_width=med(s.shoulder_width for s in samples),
        head_drop=med(s.head_drop for s in samples),
        shoulder_tilt_deg=med(s.shoulder_tilt_deg for s in samples),
    )


class _LandmarkLike:
    """Structural type: anything with .x, .y, .visibility."""

    x: float
    y: float
    visibility: float


def metrics_from_landmarks(landmarks: Sequence[_LandmarkLike]) -> PostureMetrics | None:
    """Build metrics from one pose's landmarks; None if the upper body isn't visible."""
    if len(landmarks) <= RIGHT_SHOULDER:
        return None
    nose = landmarks[NOSE]
    ls = landmarks[LEFT_SHOULDER]
    rs = landmarks[RIGHT_SHOULDER]
    for lm in (nose, ls, rs):
        if getattr(lm, "visibility", 1.0) < 0.5:
            return None

    shoulder_mid_y = (ls.y + rs.y) / 2.0
    dx = ls.x - rs.x
    dy = ls.y - rs.y
    shoulder_width = math.hypot(dx, dy)
    if shoulder_width < 1e-6:
        return None
    tilt = math.degrees(math.atan2(dy, dx))
    # atan2 of a roughly horizontal line: fold to [-90, 90]
    if tilt > 90:
        tilt -= 180
    elif tilt < -90:
        tilt += 180

    return PostureMetrics(
        nose_x=nose.x,
        nose_y=nose.y,
        shoulder_mid_y=shoulder_mid_y,
        shoulder_width=shoulder_width,
        head_drop=nose.y - shoulder_mid_y,
        shoulder_tilt_deg=tilt,
    )
