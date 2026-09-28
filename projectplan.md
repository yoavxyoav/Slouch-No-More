# posture-guard — POC plan

macOS menu bar app that watches the webcam and alerts when posture is bad.

## Decisions (2026-09-28 12:41)
- Menu bar app: Python + rumps
- Detection: MediaPipe Tasks PoseLandmarker (v1.0 API — legacy solutions API is gone), lite model in `models/`
- Two-point calibration: capture a GOOD pose and a SLOUCH pose; classify by nearest centroid
- Camera-move detection: readings persistently far from BOTH centroids (while a person is visible) => camera moved; try to auto-match a saved profile, else ask to recalibrate
- Profiles saved per camera angle in `~/.posture-guard/profiles.json`; auto-switch on match
- Alerts: macOS notification (osascript), sound (afplay), menu bar icon change — each toggleable via menu, persisted in `~/.posture-guard/config.json`
- All local, no frames stored

## Todos (2026-09-28 12:41)
- [x] Scaffold uv project, install mediapipe/opencv/rumps, verify Tasks API, download model
- [x] metrics.py — feature extraction from pose landmarks
- [x] detector.py — camera + PoseLandmarker wrapper
- [x] calibration.py — profiles (good+slouch centroids), store/load, matching
- [x] judge.py — nearest-centroid classification + debounced state machine (GOOD/SLOUCH/AWAY/CAMERA_MOVED)
- [x] alerts.py — notification/sound/icon channels, toggleable
- [x] config.py + logging_setup.py (JSON logger to logs/)
- [x] app.py — rumps menu bar app, calibration flow, worker thread
- [x] Unit tests for judge + calibration math (19 tests)
- [x] README, mypy pass

## Review (2026-09-28 12:48)
POC complete. All modules built and verified: 19 unit tests pass, mypy clean,
live camera smoke test returns real pose landmarks.

Notable during build:
- mediapipe 1.0.1 crashes on macOS in PoseLandmarker even with CPU delegate —
  pinned `<1.0` (0.10.35). Documented in bugfix.md.
- Two-point calibration (good + slouch) classified by nearest centroid with
  per-feature z-distance; UNKNOWN (far from both, person visible) sustained 8s
  is the camera-moved signal; app then tries auto-matching saved profiles
  before asking to recalibrate.
- Not committed yet — pre-commit review gate not run (user to decide when
  POC graduates).

Next candidates (not started): tune thresholds on real use, camera open/close
on demand (light stays on now), threshold tuning UI, launch-at-login.
