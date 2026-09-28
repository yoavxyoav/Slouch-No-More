# Algorithms in Posture Guard (plain-language)

## The posture classifier: projection onto the good->slouch axis

### The setup

Every camera frame becomes 6 numbers ("features"): where your nose is
(left/right and up/down), where your shoulder midpoint is, how wide your
shoulders look (a proxy for how close you lean), how far your nose has sunk
toward your shoulders, and how tilted your shoulder line is.

During calibration we collect ~36 frames of you sitting well and ~36 frames
of you slouching. For each posture we compute the average of each feature
(the "centroid" — think: the center of a cloud of points) and how much each
feature wobbles (the standard deviation — how noisy that feature is).

### Z-scores: measuring in "wobbles" instead of raw units

Raw feature values aren't comparable — a 0.05 change in nose height is huge,
a 5-degree shoulder tilt is nothing. So every measurement is converted to a
z-score: "how many typical wobbles away from the calibrated average is this?"
A z-score of 3 means "3x further than this feature normally jitters" — a real
change, in any feature.

### Why we don't just ask "which centroid is closer"

The first version classified by distance to the nearest centroid, averaged
over all 6 features. That failed in practice: when you slouch, only 2-3
features actually move (nose drops, head sinks). The other features just
add noise to the average, and the real signal gets diluted — measured live,
a clear slouch scored a separation of only 1.45 because 4 flat features
dragged down 2 strong ones.

### The fix: one axis, one number

Draw an arrow in feature-space from the GOOD centroid to the SLOUCH
centroid. That arrow IS "the direction of slouching" for your body and your
camera angle. Now project every new reading onto that arrow (like casting
its shadow onto the arrow) and read off a single number **t**:

- **t = 0** — you're exactly at your calibrated GOOD posture
- **t = 1** — exactly at your calibrated SLOUCH
- **t >= 0.5** — closer to the slouch end => classified SLOUCH
- **t < 0.5** — closer to the good end => classified GOOD

Features that don't move when you slouch are perpendicular to the arrow, so
they contribute nothing to t. Noise can no longer dilute the signal. (This
is the core idea behind Linear Discriminant Analysis, done with a ruler
instead of matrix algebra.)

### The residual: detecting a moved camera

The projection also gives a second number free of charge: the **residual** —
how far the reading is from the arrow sideways. Normal sitting, good or bad,
stays near the arrow. If the laptop lid moves, everything shifts in a
direction that has nothing to do with slouching => big residual => the
reading "doesn't look like either calibrated posture" => the UNKNOWN state,
which after 8 sustained seconds triggers the camera-moved flow (try saved
profiles, else ask to recalibrate).

### Separation: grading a calibration

The length of the good->slouch arrow (in z-score units) says how
distinguishable your two calibrated postures are. Short arrow = the clouds
overlap = classification would be a coin flip, so the app rejects the
calibration and asks for a more dramatic slouch. Current bar: 2.5.

### Smoothing: the 3-second median

Classification runs on the median of the last ~3 seconds of readings, not on
single frames. A median ignores outliers completely (one glitched frame out
of six has zero effect), which is why it beats averaging here.

### Debouncing: the 10-second rule

State must persist before the app acts: slouch sustained 10s => alert
(re-nag every 60s); out-of-distribution sustained 8s => camera-moved. This
turns a noisy per-second signal into calm, rare notifications.
