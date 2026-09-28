"""Posture classification and the debounced alerting state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from posture_guard.calibration import Profile
from posture_guard.metrics import PostureMetrics


class Posture(Enum):
    GOOD = "good"
    SLOUCH = "slouch"
    UNKNOWN = "unknown"  # person visible but far from both clusters
    AWAY = "away"  # no person in frame
    UNCALIBRATED = "uncalibrated"


class Event(Enum):
    SLOUCH_ALERT = "slouch_alert"
    CAMERA_MOVED = "camera_moved"
    BACK_TO_GOOD = "back_to_good"


def classify(
    metrics: PostureMetrics | None, profile: Profile | None, unknown_threshold: float
) -> Posture:
    """Classify by position along the calibrated good->slouch axis.

    t < 0.5 is the GOOD side, t >= 0.5 the SLOUCH side. Readings far OFF the
    axis (residual), or absurdly far along it, don't look like either
    calibrated posture => UNKNOWN (the camera-probably-moved signal).
    """
    if metrics is None:
        return Posture.AWAY
    if profile is None:
        return Posture.UNCALIBRATED
    t, residual = profile.project(metrics)
    if residual > unknown_threshold or t < -2.0 or t > 3.0:
        return Posture.UNKNOWN
    return Posture.SLOUCH if t >= 0.5 else Posture.GOOD


@dataclass
class Supervisor:
    """Turns a stream of per-tick classifications into debounced events.

    - SLOUCH must be sustained `slouch_alert_seconds` before alerting,
      then re-alerts every `alert_repeat_seconds` while it persists
      (0 = alert only once per slouch episode).
    - UNKNOWN sustained `camera_move_seconds` => CAMERA_MOVED (the app then
      tries to match a saved profile before asking to recalibrate).
    """

    slouch_alert_seconds: float = 10.0
    alert_repeat_seconds: float = 60.0
    camera_move_seconds: float = 8.0

    _state: Posture = Posture.AWAY
    _state_since: float = 0.0
    _last_slouch_alert: float = field(default=-1.0)
    _slouch_alerted: bool = False
    _camera_move_fired: bool = False

    @property
    def state(self) -> Posture:
        return self._state

    def reset(self, now: float) -> None:
        """Call after recalibration or a profile switch."""
        self._state = Posture.AWAY
        self._state_since = now
        self._slouch_alerted = False
        self._camera_move_fired = False

    def tick(self, now: float, posture: Posture) -> list[Event]:
        events: list[Event] = []
        if posture is not self._state:
            if self._state is Posture.SLOUCH and posture is Posture.GOOD and self._slouch_alerted:
                events.append(Event.BACK_TO_GOOD)
            self._state = posture
            self._state_since = now
            self._slouch_alerted = False
            self._camera_move_fired = False
            return events

        held = now - self._state_since
        if posture is Posture.SLOUCH and held >= self.slouch_alert_seconds:
            repeat_due = (
                self.alert_repeat_seconds > 0
                and now - self._last_slouch_alert >= self.alert_repeat_seconds
            )
            if not self._slouch_alerted or repeat_due:
                self._slouch_alerted = True
                self._last_slouch_alert = now
                events.append(Event.SLOUCH_ALERT)
        elif posture is Posture.UNKNOWN and held >= self.camera_move_seconds:
            if not self._camera_move_fired:
                self._camera_move_fired = True
                events.append(Event.CAMERA_MOVED)
        return events
