"""User configuration, persisted to ~/.posture-guard/config.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".posture-guard" / "config.json"


@dataclass
class Config:
    # alert channels (each toggleable from the menu)
    alert_notification: bool = True
    alert_sound: bool = True
    alert_icon: bool = True
    back_to_good_chime: bool = True  # soft chime when posture recovers after an alert
    voice_guidance: bool = True  # spoken instructions + countdown during calibration

    # capture
    capture_mode: str = "continuous"  # "continuous" | "snapshot"
    snapshot_interval: float = 15.0  # snapshot mode: seconds between camera opens

    # timing
    poll_interval: float = 0.5  # continuous mode: seconds between samples
    slouch_alert_seconds: float = 10.0  # slouch must persist this long
    alert_repeat_seconds: float = 60.0  # re-nag interval while still slouching; 0 = once only
    camera_move_seconds: float = 8.0  # sustained out-of-distribution => camera moved

    # classification (z-norm units, comparable to the calibration separation)
    unknown_threshold: float = 8.0  # off-axis residual beyond this => unknown
    match_threshold: float = 8.0  # max distance to auto-adopt a saved profile
    separation_min: float = 2.5  # min good->slouch axis length to accept a calibration

    # hardware / sounds
    camera_index: int = 0
    sound_slouch: str = "/System/Library/Sounds/Basso.aiff"
    sound_info: str = "/System/Library/Sounds/Glass.aiff"

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @staticmethod
    def load(path: Path = DEFAULT_CONFIG_PATH) -> "Config":
        if not path.exists():
            return Config()
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return Config()
        known = {f.name for f in fields(Config)}
        return Config(**{k: v for k, v in raw.items() if k in known})
