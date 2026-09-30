"""User configuration, persisted to ~/.posture-guard/config.json.

Every way a value can get in (the Advanced settings dialog, a hand-edited
config file) goes through the same parsing and validation here, so garbage
never reaches the parts of the app that sleep, divide, or compare on it.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, fields
from functools import cache
from pathlib import Path
from typing import Any, get_type_hints

logger = logging.getLogger("posture_guard.config")

DEFAULT_CONFIG_PATH = Path.home() / ".posture-guard" / "config.json"

CAPTURE_MODES = ("continuous", "snapshot")

# Inclusive [min, max] bounds for every numeric field. One table so the
# settings dialog, the loader, and the tests all agree on what is sane.
NUMERIC_LIMITS: dict[str, tuple[float, float]] = {
    "snapshot_interval": (1, 3600),
    "poll_interval": (0.1, 10),
    "slouch_alert_seconds": (1, 3600),
    "alert_repeat_seconds": (0, 3600),  # 0 = alert once only
    "camera_move_seconds": (1, 600),
    "unknown_threshold": (0.1, 100),
    "match_threshold": (0.1, 100),
    "separation_min": (0.1, 100),
    "camera_index": (0, 16),
}

_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")


@dataclass
class Config:
    # alert channels (each toggleable from the menu)
    alert_notification: bool = True
    alert_sound: bool = True
    alert_icon: bool = True
    back_to_good_chime: bool = True  # soft chime when posture recovers after an alert
    voice_guidance: bool = True  # spoken instructions + countdown during calibration
    skip_calibration_intro: bool = False  # skip the welcome/explanation speech

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
        if not isinstance(raw, dict):
            return Config()
        return Config.from_dict(raw)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Config":
        """Build a config from JSON data.

        Unknown keys are ignored; every field that is missing, mistyped, or
        out of range falls back to its default (and is logged).
        """
        config = Config()
        types = field_types()
        for f in fields(Config):
            if f.name not in raw:
                continue
            value = raw[f.name]
            if types[f.name] is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            setattr(config, f.name, value)
        defaults = Config()
        for name, problem in config.validate().items():
            fallback = getattr(defaults, name)
            logger.warning("config %s: %s - using default %r", name, problem, fallback)
            setattr(config, name, fallback)
        return config

    def validate(self) -> dict[str, str]:
        """Return {field: problem} for every invalid value; empty means all good."""
        errors: dict[str, str] = {}
        types = field_types()
        for f in fields(self):
            value = getattr(self, f.name)
            expected = types[f.name]
            if not _has_type(value, expected):
                errors[f.name] = f"expected {expected.__name__}, got {value!r}"
            elif f.name in NUMERIC_LIMITS:
                lo, hi = NUMERIC_LIMITS[f.name]
                if not lo <= value <= hi:
                    errors[f.name] = f"must be between {lo:g} and {hi:g} (got {value:g})"
            elif f.name == "capture_mode" and value not in CAPTURE_MODES:
                errors[f.name] = f"must be one of: {', '.join(CAPTURE_MODES)}"
            elif f.name.startswith("sound_") and not Path(value).is_file():
                errors[f.name] = f"file not found: {value}"
        return errors


@cache
def field_types() -> dict[str, type]:
    """Declared type of every Config field (resolved despite postponed annotations)."""
    return {f.name: get_type_hints(Config)[f.name] for f in fields(Config)}


def _has_type(value: Any, expected: type) -> bool:
    # bool is an int subclass, so check it explicitly before the numeric types
    if expected is bool:
        return isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def parse_field(name: str, raw: str) -> Any:
    """Turn user-typed text into the field's declared type.

    Raises ValueError with a message that already names the field.
    """
    expected = field_types()[name]
    text = raw.strip()
    if expected is bool:
        if text.lower() in _TRUE_WORDS:
            return True
        if text.lower() in _FALSE_WORDS:
            return False
        raise ValueError(f"'{raw}' is not true/false")
    if expected is int:
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"'{raw}' is not a whole number") from None
    if expected is float:
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"'{raw}' is not a number") from None
        if not math.isfinite(value):
            raise ValueError(f"'{raw}' is not a finite number")
        return value
    return text


def parse_settings(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Parse and validate a form's worth of values.

    ``raw`` maps field name -> typed value (checkbox/popup) or text (text
    field). Returns (parsed values, {field: problem}). Values that failed to
    parse are left out of the first dict; everything that parsed is still
    range-checked so the user sees all problems in one round.
    """
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, value in raw.items():
        try:
            values[name] = parse_field(name, value) if isinstance(value, str) else value
        except ValueError as exc:
            errors[name] = str(exc)
    candidate = Config(**values)
    for name, problem in candidate.validate().items():
        if name in values:
            errors.setdefault(name, problem)
    return values, errors
