import json
from pathlib import Path

from posture_guard.calibration import ProfileStore, new_profile
from posture_guard.metrics import PostureMetrics


def make_metrics(nose_y: float, nose_x: float = 0.5) -> PostureMetrics:
    return PostureMetrics(
        nose_x=nose_x,
        nose_y=nose_y,
        shoulder_mid_x=nose_x,
        shoulder_mid_y=nose_y + 0.25,
        shoulder_width=0.3,
        head_drop=-0.25,
        shoulder_tilt_deg=0.0,
    )


def samples(nose_y: float, nose_x: float = 0.5) -> list[PostureMetrics]:
    return [make_metrics(nose_y + d, nose_x) for d in (-0.01, 0.0, 0.01)]


def test_roundtrip_persistence(tmp_path: Path) -> None:
    store = ProfileStore(path=tmp_path / "profiles.json")
    profile = new_profile("desk", samples(0.35), samples(0.48))
    store.add(profile)

    reloaded = ProfileStore(path=tmp_path / "profiles.json")
    assert reloaded.active is not None
    assert reloaded.active.name == "desk"
    assert reloaded.active.good.mean == profile.good.mean
    assert reloaded.active.slouch.std == profile.slouch.std


def test_corrupt_store_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text("{not json")
    store = ProfileStore(path=path)
    assert store.profiles == []
    assert store.active is None


def test_activate_and_delete(tmp_path: Path) -> None:
    store = ProfileStore(path=tmp_path / "profiles.json")
    p1 = new_profile("a", samples(0.35), samples(0.48))
    p2 = new_profile("b", samples(0.20), samples(0.33))
    p2.profile_id = "p-other"
    store.add(p1)
    store.add(p2)
    assert store.active is p2
    store.activate(p1.profile_id)
    assert store.active is p1
    store.delete(p1.profile_id)
    assert store.active is p2


def test_best_match_picks_right_angle(tmp_path: Path) -> None:
    store = ProfileStore(path=tmp_path / "profiles.json")
    lid_open = new_profile("lid open", samples(0.35), samples(0.48))
    lid_tilted = new_profile("lid tilted", samples(0.15, nose_x=0.4), samples(0.28, nose_x=0.4))
    lid_tilted.profile_id = "p-tilted"
    store.add(lid_open)
    store.add(lid_tilted)

    # live readings look like the "lid open" angle, near its good cluster
    recent = samples(0.352)
    match = store.best_match(recent, threshold=4.0)
    assert match is not None
    assert match.name == "lid open"


def test_best_match_none_for_new_angle(tmp_path: Path) -> None:
    store = ProfileStore(path=tmp_path / "profiles.json")
    store.add(new_profile("desk", samples(0.35), samples(0.48)))
    # a genuinely new camera angle: far from anything saved
    recent = samples(0.9, nose_x=0.05)
    assert store.best_match(recent, threshold=4.0) is None


def test_store_file_is_valid_json(tmp_path: Path) -> None:
    store = ProfileStore(path=tmp_path / "profiles.json")
    store.add(new_profile("desk", samples(0.35), samples(0.48)))
    payload = json.loads((tmp_path / "profiles.json").read_text())
    assert payload["active_id"]
    assert len(payload["profiles"]) == 1


def test_separation_low_for_similar_postures() -> None:
    profile = new_profile("bad", samples(0.35), samples(0.352))
    assert profile.separation() < 2.5


def test_separation_high_for_distinct_postures() -> None:
    profile = new_profile("good", samples(0.35), samples(0.48))
    assert profile.separation() >= 2.5


def test_clear_all(tmp_path: Path) -> None:
    store = ProfileStore(path=tmp_path / "profiles.json")
    store.add(new_profile("a", samples(0.35), samples(0.48)))
    store.add(new_profile("b", samples(0.20), samples(0.33)))
    store.clear_all()
    assert store.profiles == []
    assert store.active is None
    reloaded = ProfileStore(path=tmp_path / "profiles.json")
    assert reloaded.profiles == []


def test_schema_change_invalidates_profiles(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    store = ProfileStore(path=path)
    store.add(new_profile("desk", samples(0.35), samples(0.48)))
    payload = json.loads(path.read_text())
    payload["feature_names"] = ["old_feature"] + payload["feature_names"][1:]
    path.write_text(json.dumps(payload))
    reloaded = ProfileStore(path=path)
    assert reloaded.profiles == []
