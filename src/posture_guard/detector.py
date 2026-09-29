"""Webcam capture + MediaPipe PoseLandmarker (Tasks API, mediapipe >= 1.0)."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from posture_guard.metrics import (
    LEFT_EAR,
    LEFT_EYE,
    LEFT_SHOULDER,
    MOUTH_LEFT,
    MOUTH_RIGHT,
    NOSE,
    RIGHT_EAR,
    RIGHT_EYE,
    RIGHT_SHOULDER,
    PostureMetrics,
    metrics_from_landmarks,
)

DRAWN_LANDMARKS = (
    NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR,
    MOUTH_LEFT, MOUTH_RIGHT, LEFT_SHOULDER, RIGHT_SHOULDER,
)

# synthetic display-only points (pose model has no crown/chin landmarks);
# negative keys so they can't collide with real landmark indices
HEAD_TOP = -1
CHIN = -2

logger = logging.getLogger("posture_guard.detector")

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "pose_landmarker_lite.task"


class PoseDetector:
    def __init__(self, camera_index: int = 0, model_path: Path = MODEL_PATH) -> None:
        self.camera_index = camera_index
        self.model_path = model_path
        self._cap: cv2.VideoCapture | None = None
        self._landmarker: vision.PoseLandmarker | None = None
        # last frame + landmark pixel coords (keyed by landmark index), for
        # the calibration preview; only touched from the worker thread
        self._last_frame: np.ndarray | None = None
        self._last_points: dict[int, tuple[int, int]] = {}

    def start(self) -> None:
        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=str(self.model_path),
                # Metal (GPU) delegate crashes on macOS with mediapipe 1.0.x
                delegate=mp_python.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self.open_camera()

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    def open_camera(self, warmup_frames: int = 0) -> None:
        if self._cap is not None:
            return
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"could not open camera {self.camera_index} - check camera permissions"
            )
        # let auto-exposure settle so snapshots aren't dark
        for _ in range(warmup_frames):
            cap.read()
        self._cap = cap
        logger.info("camera %s opened", self.camera_index)

    def close_camera(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("camera released")

    def read(self) -> PostureMetrics | None:
        """Grab one frame and extract posture metrics. None = no usable pose."""
        if self._cap is None or self._landmarker is None:
            raise RuntimeError("detector not started")
        ok, frame = self._cap.read()
        if not ok:
            logger.warning("frame grab failed")
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(image)
        self._last_frame = frame
        if not result.pose_landmarks:
            self._last_points = {}
            return None
        landmarks = result.pose_landmarks[0]
        h, w = frame.shape[:2]
        points = {
            i: (int(landmarks[i].x * w), int(landmarks[i].y * h))
            for i in DRAWN_LANDMARKS
            if i < len(landmarks) and landmarks[i].visibility >= 0.5
        }
        # synthesize crown and chin from face geometry (no such landmarks exist)
        if LEFT_EAR in points and RIGHT_EAR in points:
            le, re_ = points[LEFT_EAR], points[RIGHT_EAR]
            ear_span = math.hypot(le[0] - re_[0], le[1] - re_[1])
            if LEFT_EYE in points and RIGHT_EYE in points:
                eye_mid_x = (points[LEFT_EYE][0] + points[RIGHT_EYE][0]) // 2
                eye_mid_y = (points[LEFT_EYE][1] + points[RIGHT_EYE][1]) // 2
                points[HEAD_TOP] = (eye_mid_x, int(eye_mid_y - 0.9 * ear_span))
            if MOUTH_LEFT in points and MOUTH_RIGHT in points:
                mouth_mid_x = (points[MOUTH_LEFT][0] + points[MOUTH_RIGHT][0]) // 2
                mouth_mid_y = (points[MOUTH_LEFT][1] + points[MOUTH_RIGHT][1]) // 2
                points[CHIN] = (mouth_mid_x, int(mouth_mid_y + 0.4 * ear_span))
        self._last_points = points
        return metrics_from_landmarks(landmarks)

    @property
    def last_points(self) -> dict[int, tuple[int, int]]:
        return dict(self._last_points)

    @staticmethod
    def _draw_pose(
        frame: np.ndarray,
        points: dict[int, tuple[int, int]],
        color: tuple[int, int, int],
        filled: bool,
    ) -> None:
        h, w = frame.shape[:2]
        radius, thickness = (8, -1) if filled else (10, 2)
        for i, (x, y) in points.items():
            r = radius if i in (NOSE, LEFT_SHOULDER, RIGHT_SHOULDER) else radius - 3
            cv2.circle(frame, (w - x, y), r, color, thickness)
        if LEFT_SHOULDER in points and RIGHT_SHOULDER in points:
            ls, rs = points[LEFT_SHOULDER], points[RIGHT_SHOULDER]
            cv2.line(frame, (w - ls[0], ls[1]), (w - rs[0], rs[1]), color, 3 if filled else 2)

    @property
    def frame_size(self) -> tuple[int, int] | None:
        """(width, height) of the last frame, if any. Worker thread only."""
        if self._last_frame is None:
            return None
        h, w = self._last_frame.shape[:2]
        return w, h

    def annotated_frame(
        self,
        text: str,
        ghosts: list[tuple[dict[int, tuple[int, int]], tuple[int, int, int], str]] | None = None,
        sub_text: str | None = None,
        sub_ok: bool = False,
        border: tuple[int, int, int] | None = None,
    ) -> bytes | None:
        """Last frame, mirrored, with tracked points and a status banner,
        JPEG-encoded for the preview window. Each ghost is (points, color,
        label) drawn hollow for comparison (e.g. a captured or saved pose);
        `sub_text` is a second banner line (green when `sub_ok`); `border`
        tints the frame edge to signal the calibration phase. Worker thread only."""
        if self._last_frame is None:
            return None
        frame = cv2.flip(self._last_frame, 1)
        h, w = frame.shape[:2]
        for idx, (points, color, label) in enumerate(ghosts or []):
            self._draw_pose(frame, points, color, filled=False)
            cv2.putText(frame, label, (20, h - 20 - 30 * idx),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        self._draw_pose(frame, self._last_points, (80, 220, 80), filled=True)
        if not self._last_points:
            cv2.putText(frame, "NOT DETECTING A PERSON", (30, h - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        banner_h = 110 if sub_text else 70
        cv2.rectangle(frame, (0, 0), (w, banner_h), (30, 30, 30), -1)
        cv2.putText(frame, text, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (255, 255, 255), 2)
        if sub_text:
            color = (80, 220, 80) if sub_ok else (60, 160, 255)
            cv2.putText(frame, sub_text, (20, 90), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, color, 2)
        if border is not None:
            cv2.rectangle(frame, (0, 0), (w - 1, h - 1), border, 12)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def stop(self) -> None:
        self.close_camera()
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
