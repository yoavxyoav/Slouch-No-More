"""Calibration profiles: a GOOD and a SLOUCH centroid per camera angle.

A profile captures how the scene looks from one lid angle / seating position.
Profiles are persisted to ~/.posture-guard/profiles.json so moving the lid
back to a known angle never requires recalibrating: we match the live
readings against saved profiles and switch automatically.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from posture_guard.metrics import FEATURE_NAMES, PostureMetrics

# Minimum per-feature std used in z-distance, so a rock-steady calibration
# doesn't make the classifier hypersensitive.
STD_FLOOR = 0.015

DEFAULT_STORE_PATH = Path.home() / ".posture-guard" / "profiles.json"


@dataclass
class FeatureStats:
    mean: list[float]
    std: list[float]

    @staticmethod
    def from_samples(samples: list[PostureMetrics]) -> "FeatureStats":
        if not samples:
            raise ValueError("no samples to calibrate from")
        vectors = [s.to_vector() for s in samples]
        n = len(vectors)
        dim = len(vectors[0])
        mean = [sum(v[i] for v in vectors) / n for i in range(dim)]
        std = [
            max(math.sqrt(sum((v[i] - mean[i]) ** 2 for v in vectors) / n), STD_FLOOR)
            for i in range(dim)
        ]
        return FeatureStats(mean=mean, std=std)

    def z_distance(self, vector: list[float]) -> float:
        """RMS of per-feature z-scores of `vector` against this cluster."""
        acc = 0.0
        for x, mu, sigma in zip(vector, self.mean, self.std):
            z = (x - mu) / sigma
            acc += z * z
        return math.sqrt(acc / len(self.mean))

    def z_norm(self, vector: list[float]) -> float:
        """Euclidean norm of the z-score vector (not divided by dim) -
        comparable to Profile.separation()'s axis-length units."""
        acc = 0.0
        for x, mu, sigma in zip(vector, self.mean, self.std):
            z = (x - mu) / sigma
            acc += z * z
        return math.sqrt(acc)


@dataclass
class Profile:
    profile_id: str
    name: str
    good: FeatureStats
    slouch: FeatureStats
    created_at: float = field(default_factory=time.time)

    def _pooled_std(self) -> list[float]:
        return [
            max((g + s) / 2, STD_FLOOR) for g, s in zip(self.good.std, self.slouch.std)
        ]

    def _axis(self) -> tuple[list[float], float]:
        """Direction good->slouch in z-space, and its length (the separation)."""
        std = self._pooled_std()
        d = [
            (s - g) / sd for g, s, sd in zip(self.good.mean, self.slouch.mean, std)
        ]
        return d, math.sqrt(sum(x * x for x in d))

    def project(self, metrics: PostureMetrics) -> tuple[float, float]:
        """Project a reading onto the good->slouch axis.

        Returns (t, residual): t = 0 at the GOOD centroid, 1 at the SLOUCH
        centroid; residual = off-axis distance in z-units. Slouch-irrelevant
        features contribute nothing to t, so noise can't dilute the signal.
        """
        std = self._pooled_std()
        z = [(x - g) / sd for x, g, sd in zip(metrics.to_vector(), self.good.mean, std)]
        d, length = self._axis()
        if length < 1e-9:
            return 0.0, math.sqrt(sum(v * v for v in z))
        t = sum(zi * di for zi, di in zip(z, d)) / (length * length)
        along_sq = (t * length) ** 2
        norm_sq = sum(v * v for v in z)
        residual = math.sqrt(max(norm_sq - along_sq, 0.0))
        return t, residual

    def min_distance(self, metrics: PostureMetrics) -> float:
        """Z-space distance to the nearest of the two centroids (pooled std)."""
        std = self._pooled_std()
        v = metrics.to_vector()
        d_good = math.sqrt(sum(((x - m) / sd) ** 2 for x, m, sd in zip(v, self.good.mean, std)))
        d_slouch = math.sqrt(sum(((x - m) / sd) ** 2 for x, m, sd in zip(v, self.slouch.mean, std)))
        return min(d_good, d_slouch)

    def separation(self) -> float:
        """Length of the good->slouch axis in z-units: how distinguishable the
        two calibrated postures are along the direction that matters."""
        return self._axis()[1]


def new_profile(name: str, good: list[PostureMetrics], slouch: list[PostureMetrics]) -> Profile:
    return Profile(
        profile_id=f"p{int(time.time())}",
        name=name,
        good=FeatureStats.from_samples(good),
        slouch=FeatureStats.from_samples(slouch),
    )


class ProfileStore:
    def __init__(self, path: Path = DEFAULT_STORE_PATH) -> None:
        self.path = path
        self.profiles: list[Profile] = []
        self.active_id: str | None = None
        self._load()

    @property
    def active(self) -> Profile | None:
        for p in self.profiles:
            if p.profile_id == self.active_id:
                return p
        return None

    def add(self, profile: Profile, activate: bool = True) -> None:
        self.profiles.append(profile)
        if activate:
            self.active_id = profile.profile_id
        self.save()

    def activate(self, profile_id: str) -> None:
        if any(p.profile_id == profile_id for p in self.profiles):
            self.active_id = profile_id
            self.save()

    def clear_all(self) -> None:
        self.profiles = []
        self.active_id = None
        self.save()

    def delete(self, profile_id: str) -> None:
        self.profiles = [p for p in self.profiles if p.profile_id != profile_id]
        if self.active_id == profile_id:
            self.active_id = self.profiles[-1].profile_id if self.profiles else None
        self.save()

    def best_match(
        self, recent: list[PostureMetrics], threshold: float
    ) -> Profile | None:
        """Find the saved profile whose good/slouch cluster best explains
        `recent` readings (median of min-distances). Used after a camera move
        to avoid recalibrating for a known lid angle."""
        best: Profile | None = None
        best_score = threshold
        for p in self.profiles:
            dists = sorted(p.min_distance(m) for m in recent)
            score = dists[len(dists) // 2]  # median
            if score < best_score:
                best_score = score
                best = p
        return best

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_id": self.active_id,
            "feature_names": FEATURE_NAMES,
            "profiles": [
                {
                    "profile_id": p.profile_id,
                    "name": p.name,
                    "created_at": p.created_at,
                    "good": {"mean": p.good.mean, "std": p.good.std},
                    "slouch": {"mean": p.slouch.mean, "std": p.slouch.std},
                }
                for p in self.profiles
            ],
        }
        self.path.write_text(json.dumps(payload, indent=2))

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if payload.get("feature_names") != FEATURE_NAMES:
            # the feature schema changed: stored centroids no longer line up
            # with live vectors, so the profiles must be recalibrated
            return
        self.profiles = [
            Profile(
                profile_id=raw["profile_id"],
                name=raw["name"],
                created_at=raw.get("created_at", 0.0),
                good=FeatureStats(**raw["good"]),
                slouch=FeatureStats(**raw["slouch"]),
            )
            for raw in payload.get("profiles", [])
        ]
        self.active_id = payload.get("active_id")
