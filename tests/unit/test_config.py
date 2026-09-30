"""Config parsing, validation, and load-time fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from posture_guard.config import (
    ADVANCED_FIELDS,
    CAPTURE_MODES,
    NUMERIC_LIMITS,
    Config,
    field_types,
    parse_field,
    parse_settings,
)


def test_defaults_are_valid() -> None:
    assert Config().validate() == {}


def test_every_numeric_field_has_limits() -> None:
    numeric = {n for n, t in field_types().items() if t in (int, float)}
    assert numeric == set(NUMERIC_LIMITS)


def test_advanced_fields_are_real_and_not_in_the_menu() -> None:
    assert set(ADVANCED_FIELDS) <= set(field_types())
    menu_fields = {
        "alert_notification", "alert_sound", "alert_icon", "capture_mode",
        "sound_slouch", "sound_info", "slouch_alert_seconds",
        "alert_repeat_seconds", "voice_guidance", "skip_calibration_intro",
    }
    assert not menu_fields & set(ADVANCED_FIELDS)


def test_parse_settings_accepts_a_subset_of_fields() -> None:
    values, errors = parse_settings({"poll_interval": "0.25", "camera_index": "1"})
    assert errors == {}
    assert values == {"poll_interval": 0.25, "camera_index": 1}


# ---------- parse_field ----------


@pytest.mark.parametrize("raw,expected", [("true", True), ("Yes", True), ("1", True),
                                          ("false", False), ("no", False), ("0", False)])
def test_parse_bool_words(raw: str, expected: bool) -> None:
    assert parse_field("alert_sound", raw) is expected


def test_parse_bool_rejects_junk() -> None:
    with pytest.raises(ValueError):
        parse_field("alert_sound", "maybe")


def test_parse_float_and_int() -> None:
    assert parse_field("poll_interval", " 0.75 ") == 0.75
    assert parse_field("camera_index", "2") == 2


@pytest.mark.parametrize("raw", ["abc", "", "nan", "inf", "1e999"])
def test_parse_float_rejects_junk(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_field("poll_interval", raw)


@pytest.mark.parametrize("raw", ["1.5", "x", ""])
def test_parse_int_rejects_junk(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_field("camera_index", raw)


def test_parse_str_passthrough() -> None:
    assert parse_field("capture_mode", "snapshot") == "snapshot"


# ---------- validate ----------


@pytest.mark.parametrize("name", sorted(NUMERIC_LIMITS))
def test_numeric_bounds_are_enforced(name: str) -> None:
    lo, hi = NUMERIC_LIMITS[name]
    kind = field_types()[name]
    config = Config()
    setattr(config, name, kind(lo))
    assert name not in config.validate()
    setattr(config, name, kind(hi))
    assert name not in config.validate()
    setattr(config, name, kind(lo) - 1)
    assert name in config.validate()
    setattr(config, name, kind(hi) + 1)
    assert name in config.validate()


def test_validate_catches_wrong_types() -> None:
    config = Config()
    config.poll_interval = "fast"  # type: ignore[assignment]
    config.alert_sound = 1  # type: ignore[assignment]
    config.camera_index = 1.5  # type: ignore[assignment]
    errors = config.validate()
    assert set(errors) == {"poll_interval", "alert_sound", "camera_index"}


def test_validate_capture_mode() -> None:
    config = Config()
    for mode in CAPTURE_MODES:
        config.capture_mode = mode
        assert "capture_mode" not in config.validate()
    config.capture_mode = "sometimes"
    assert "capture_mode" in config.validate()


def test_validate_sound_files_must_exist(tmp_path: Path) -> None:
    config = Config()
    config.sound_slouch = str(tmp_path / "missing.aiff")
    assert "sound_slouch" in config.validate()
    real = tmp_path / "real.aiff"
    real.write_bytes(b"")
    config.sound_slouch = str(real)
    assert "sound_slouch" not in config.validate()


# ---------- parse_settings (the dialog path) ----------


def _form(config: Config) -> dict[str, object]:
    return {
        name: (value if isinstance(value, (bool, str)) else f"{value:g}")
        for name, value in vars(config).items()
    }


def test_parse_settings_round_trips_defaults() -> None:
    values, errors = parse_settings(_form(Config()))
    assert errors == {}
    assert Config(**values) == Config()


def test_parse_settings_reports_every_problem_at_once() -> None:
    form = _form(Config())
    form["poll_interval"] = "banana"  # parse error
    form["slouch_alert_seconds"] = "0"  # out of range
    form["camera_index"] = "-1"  # out of range
    values, errors = parse_settings(form)
    assert set(errors) == {"poll_interval", "slouch_alert_seconds", "camera_index"}
    assert "poll_interval" not in values
    assert values["camera_move_seconds"] == 8.0


def test_parse_settings_keeps_typed_values() -> None:
    form = _form(Config())
    form["alert_sound"] = False
    form["capture_mode"] = "snapshot"
    values, errors = parse_settings(form)
    assert errors == {}
    assert values["alert_sound"] is False
    assert values["capture_mode"] == "snapshot"


# ---------- load ----------


def test_load_falls_back_per_field_on_bad_values(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "poll_interval": "fast",  # wrong type
        "slouch_alert_seconds": -5,  # out of range
        "camera_index": 2,  # fine
        "capture_mode": "snapshot",  # fine
        "sound_info": "/nope/missing.aiff",  # missing file
        "alert_icon": "true",  # wrong type (string, not bool)
        "unknown_key": 123,
    }))
    config = Config.load(path)
    defaults = Config()
    assert config.poll_interval == defaults.poll_interval
    assert config.slouch_alert_seconds == defaults.slouch_alert_seconds
    assert config.sound_info == defaults.sound_info
    assert config.alert_icon is defaults.alert_icon
    assert config.camera_index == 2
    assert config.capture_mode == "snapshot"
    assert config.validate() == {}


def test_load_promotes_json_ints_to_float(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"slouch_alert_seconds": 20}))
    config = Config.load(path)
    assert config.slouch_alert_seconds == 20.0
    assert isinstance(config.slouch_alert_seconds, float)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", "42"])
def test_load_ignores_unusable_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(content)
    assert Config.load(path) == Config()


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = Config(camera_index=1, poll_interval=1.25, alert_sound=False)
    config.save(path)
    assert Config.load(path) == config
