# Bug fixes

## mediapipe 1.0.1 aborts on macOS when running PoseLandmarker (2026-09-28 12:46)

**Issue:** With mediapipe 1.0.1, `PoseLandmarker.detect()` hard-crashed the
process (`Check failed: service_ Service is unavailable` from
`DrishtiMetalHelper` / `TensorsToDetectionsCalculator::Open()`). Crash occurred
even with `BaseOptions.Delegate.CPU` — the Metal helper initializes regardless
of the selected delegate in that release.

**Fix:** Pinned `mediapipe<1.0` (resolved to 0.10.35), which runs the same
Tasks API cleanly on macOS. Kept the explicit CPU delegate.

**Verified:** smoke test reads real landmarks from the webcam across 5 frames.

## Advanced settings: one dialog per field, no validation (2026-09-30 14:58)

**Issue:** Settings > Advanced... listed every config field as its own menu
item, each opening a separate single-line dialog. Values were only type-cast
(`float("nan")`, negative intervals, a camera index of 999, a capture mode of
"whatever" all saved fine), and a hand-edited config file was loaded with no
checks at all, so a string in a float field crashed later in the supervisor.

**Fix:** One NSAlert form (`settings_dialog.py`) with checkboxes for booleans,
popups for capture mode and sounds, text fields for numbers. Parsing and range
checks live in `config.py` (`NUMERIC_LIMITS`, `Config.validate`,
`parse_settings`); the form is re-shown with the user's entries and the list
of problems until everything validates. `Config.load` runs the same validation
and falls back to the default for each bad field, logging a warning.

**Verified:** 21 new unit tests in tests/unit/test_config.py (66 total pass),
mypy clean, dialog rendered and screenshotted with an injected error.
