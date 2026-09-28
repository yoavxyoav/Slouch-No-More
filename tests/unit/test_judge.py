import pytest

from posture_guard.calibration import FeatureStats, Profile, new_profile
from posture_guard.judge import Event, Posture, Supervisor, classify
from posture_guard.metrics import PostureMetrics


def make_metrics(
    nose_y: float = 0.35,
    shoulder_mid_y: float = 0.60,
    nose_x: float = 0.5,
    shoulder_width: float = 0.30,
    tilt: float = 0.0,
) -> PostureMetrics:
    return PostureMetrics(
        nose_x=nose_x,
        nose_y=nose_y,
        shoulder_mid_y=shoulder_mid_y,
        shoulder_width=shoulder_width,
        head_drop=nose_y - shoulder_mid_y,
        shoulder_tilt_deg=tilt,
    )


GOOD = make_metrics(nose_y=0.35, shoulder_mid_y=0.60)
SLOUCH = make_metrics(nose_y=0.48, shoulder_mid_y=0.62, shoulder_width=0.36)


def jitter(m: PostureMetrics, dy: float) -> PostureMetrics:
    return make_metrics(
        nose_y=m.nose_y + dy,
        shoulder_mid_y=m.shoulder_mid_y + dy / 2,
        nose_x=m.nose_x,
        shoulder_width=m.shoulder_width,
        tilt=m.shoulder_tilt_deg,
    )


@pytest.fixture
def profile() -> Profile:
    good_samples = [jitter(GOOD, d) for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    slouch_samples = [jitter(SLOUCH, d) for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    return new_profile("test", good_samples, slouch_samples)


class TestClassify:
    def test_good(self, profile: Profile) -> None:
        assert classify(GOOD, profile, unknown_threshold=6.0) is Posture.GOOD

    def test_slouch(self, profile: Profile) -> None:
        assert classify(SLOUCH, profile, unknown_threshold=6.0) is Posture.SLOUCH

    def test_away_when_no_metrics(self, profile: Profile) -> None:
        assert classify(None, profile, unknown_threshold=6.0) is Posture.AWAY

    def test_uncalibrated_without_profile(self) -> None:
        assert classify(GOOD, None, unknown_threshold=6.0) is Posture.UNCALIBRATED

    def test_unknown_when_far_from_both(self, profile: Profile) -> None:
        # camera moved: everything shifted way off both clusters
        moved = make_metrics(nose_y=0.05, shoulder_mid_y=0.95, nose_x=0.1, shoulder_width=0.6)
        assert classify(moved, profile, unknown_threshold=6.0) is Posture.UNKNOWN


class TestFeatureStats:
    def test_std_floor_applied(self) -> None:
        stats = FeatureStats.from_samples([GOOD] * 5)  # zero variance
        assert all(s >= 0.015 for s in stats.std)

    def test_zero_distance_at_mean(self) -> None:
        stats = FeatureStats.from_samples([GOOD] * 5)
        assert stats.z_distance(GOOD.to_vector()) == pytest.approx(0.0)

    def test_empty_samples_raise(self) -> None:
        with pytest.raises(ValueError):
            FeatureStats.from_samples([])


class TestSupervisor:
    def test_slouch_alert_debounced(self) -> None:
        sup = Supervisor(slouch_alert_seconds=10, alert_repeat_seconds=60)
        assert sup.tick(0, Posture.SLOUCH) == []
        assert sup.tick(5, Posture.SLOUCH) == []
        assert sup.tick(10, Posture.SLOUCH) == [Event.SLOUCH_ALERT]
        # no re-alert before repeat interval
        assert sup.tick(30, Posture.SLOUCH) == []
        assert sup.tick(70, Posture.SLOUCH) == [Event.SLOUCH_ALERT]

    def test_brief_slouch_never_alerts(self) -> None:
        sup = Supervisor(slouch_alert_seconds=10)
        sup.tick(0, Posture.SLOUCH)
        sup.tick(5, Posture.GOOD)
        assert sup.tick(6, Posture.SLOUCH) == []
        assert sup.tick(15, Posture.SLOUCH) == []  # timer restarted at t=6
        assert sup.tick(16, Posture.SLOUCH) == [Event.SLOUCH_ALERT]

    def test_back_to_good_only_after_alert(self) -> None:
        sup = Supervisor(slouch_alert_seconds=10)
        sup.tick(0, Posture.SLOUCH)
        sup.tick(10, Posture.SLOUCH)  # alert fired
        assert sup.tick(12, Posture.GOOD) == [Event.BACK_TO_GOOD]
        # without an alert, no back-to-good event
        sup.tick(20, Posture.SLOUCH)
        assert sup.tick(22, Posture.GOOD) == []

    def test_camera_moved_fires_once(self) -> None:
        sup = Supervisor(camera_move_seconds=8)
        assert sup.tick(0, Posture.UNKNOWN) == []
        assert sup.tick(8, Posture.UNKNOWN) == [Event.CAMERA_MOVED]
        assert sup.tick(20, Posture.UNKNOWN) == []
        # leaving and re-entering UNKNOWN re-arms it
        sup.tick(21, Posture.GOOD)
        sup.tick(22, Posture.UNKNOWN)
        assert sup.tick(30, Posture.UNKNOWN) == [Event.CAMERA_MOVED]

    def test_away_never_alerts(self) -> None:
        sup = Supervisor(slouch_alert_seconds=1, camera_move_seconds=1)
        assert sup.tick(0, Posture.AWAY) == []
        assert sup.tick(100, Posture.AWAY) == []


def test_median_metrics_smooths_outlier() -> None:
    from posture_guard.metrics import median_metrics

    samples = [GOOD, GOOD, make_metrics(nose_y=0.9, shoulder_mid_y=0.95)]
    med = median_metrics(samples)
    assert med is not None
    assert med.nose_y == GOOD.nose_y


def test_median_metrics_empty_is_none() -> None:
    from posture_guard.metrics import median_metrics

    assert median_metrics([]) is None


def test_repeat_zero_means_alert_only_once() -> None:
    sup = Supervisor(slouch_alert_seconds=10, alert_repeat_seconds=0)
    assert sup.tick(10, Posture.SLOUCH) == []
    assert sup.tick(20, Posture.SLOUCH) == [Event.SLOUCH_ALERT]
    assert sup.tick(500, Posture.SLOUCH) == []  # never repeats
    # a new slouch episode alerts again
    sup.tick(501, Posture.GOOD)
    sup.tick(502, Posture.SLOUCH)
    assert sup.tick(512, Posture.SLOUCH) == [Event.SLOUCH_ALERT]
