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
