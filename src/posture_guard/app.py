"""Posture Guard menu bar app (rumps).

Threading model: a worker thread polls the camera and appends metrics to a
shared deque; a rumps Timer on the main thread classifies, drives the
debounced supervisor, updates the icon, and fires alerts.
"""

from __future__ import annotations

import math
import subprocess
import sys
import threading
import time
from dataclasses import fields
from collections import deque
from datetime import datetime
from pathlib import Path

import AppKit
import Foundation
import rumps

from posture_guard.alerts import Alerter, play_sound, speak, speak_cancellable
from posture_guard.calibration import FeatureStats, ProfileStore, new_profile
from posture_guard.config import DEFAULT_CONFIG_PATH
from posture_guard.config import Config
from posture_guard.detector import PoseDetector
from posture_guard.judge import Event, Posture, Supervisor, classify
from posture_guard.logging_setup import setup_logging
from posture_guard.detector import LEFT_SHOULDER, NOSE, RIGHT_SHOULDER
from posture_guard.metrics import (
    FEATURE_NAMES,
    TILT_SCALE,
    PostureMetrics,
    median_metrics,
)

logger = setup_logging()

ICONS = {
    Posture.GOOD: "🧘",
    Posture.SLOUCH: "🔴",
    Posture.UNKNOWN: "❓",
    Posture.AWAY: "💤",
    Posture.UNCALIBRATED: "⚪",
}
ICON_PAUSED = "⏸"

CAPTURE_COUNTDOWN = 3.0
CAPTURE_WINDOW = 5.0

LOGO_PATH = Path(__file__).resolve().parents[2] / "assets" / "logo.png"
SPLASH_SECONDS = 2.5

SYSTEM_SOUNDS_DIR = Path("/System/Library/Sounds")
ALERT_DELAY_CHOICES = (5, 10, 15, 20, 30, 60)
ALERT_REPEAT_CHOICES = (0, 3, 10, 20, 60)  # 0 = just once

LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.posture-guard.plist"
LAUNCH_AGENT_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.posture-guard</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""


class PostureGuardApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Slouch No More", title=ICON_PAUSED, quit_button=None)
        self.config = Config.load()
        self.store = ProfileStore()
        self.alerter = Alerter(self.config)
        self.detector = PoseDetector(camera_index=self.config.camera_index)
        self.supervisor = Supervisor(
            slouch_alert_seconds=self.config.slouch_alert_seconds,
            alert_repeat_seconds=self.config.alert_repeat_seconds,
            camera_move_seconds=self.config.camera_move_seconds,
        )
        self.paused = False
        self.capturing = False
        self._last_logged: Posture | None = None

        # (timestamp, metrics-or-None) samples from the worker thread
        self._samples: deque[tuple[float, PostureMetrics | None]] = deque(maxlen=120)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # kicks the worker out of a snapshot-mode sleep (e.g. calibration start)
        self._wake = threading.Event()
        # set when the user presses S in the preview to skip the intro speech
        self._skip_intro = threading.Event()

        # calibration preview window (separate process, frames over stdin)
        self._preview_proc: subprocess.Popen[bytes] | None = None
        self._preview_text = ""
        self._ghost_points: dict[int, tuple[int, int]] | None = None
        self._preview_border: tuple[int, int, int] | None = None
        # when set (after the GOOD capture), the preview shows a live
        # "difference from GOOD" readout so the user can slouch until it passes
        self._gap_stats: FeatureStats | None = None
        self._viewcam = False

        self.status_item = rumps.MenuItem("Starting...")
        self.pause_item = rumps.MenuItem("Pause", callback=self.on_pause)
        self.cal_item = rumps.MenuItem(
            "Calibrate... (GOOD, then SLOUCH)", callback=self.on_calibrate
        )
        self.profiles_menu = rumps.MenuItem("Profiles")
        self.toggle_notif = rumps.MenuItem(
            "Alert: notification", callback=self.on_toggle_notif
        )
        self.toggle_sound = rumps.MenuItem("Alert: sound", callback=self.on_toggle_sound)
        self.toggle_icon = rumps.MenuItem(
            "Alert: menu bar icon", callback=self.on_toggle_icon
        )
        self.toggle_snapshot = rumps.MenuItem(
            f"Snapshot mode (camera opens every {self.config.snapshot_interval:.0f}s)",
            callback=self.on_toggle_snapshot,
        )
        self.viewcam_item = rumps.MenuItem("View camera", callback=self.on_toggle_viewcam)
        self.sound_bad_menu = rumps.MenuItem("Bad-posture sound")
        self.sound_good_menu = rumps.MenuItem("Recovery sound")
        self.delay_menu = rumps.MenuItem("Alert after slouching for...")
        self.repeat_menu = rumps.MenuItem("Repeat alert...")
        self.login_item = rumps.MenuItem("Start at login", callback=self.on_toggle_login)
        self.voice_item = rumps.MenuItem(
            "Voice guidance in calibration", callback=self.on_toggle_voice
        )
        self.intro_item = rumps.MenuItem(
            "Calibration intro speech", callback=self.on_toggle_intro
        )
        self.settings_menu = rumps.MenuItem("Settings")
        self.settings_menu.add(self.sound_bad_menu)
        self.settings_menu.add(self.sound_good_menu)
        self.settings_menu.add(self.delay_menu)
        self.settings_menu.add(self.repeat_menu)
        self.settings_menu.add(self.voice_item)
        self.settings_menu.add(self.intro_item)
        self.settings_menu.add(self.login_item)
        self.advanced_menu = rumps.MenuItem("Advanced...")
        self.settings_menu.add(self.advanced_menu)
        self.settings_menu.add(
            rumps.MenuItem("Open config file...", callback=self.on_open_config)
        )
        self.settings_menu.add(
            rumps.MenuItem("Reload config", callback=self.on_reload_config)
        )
        self._build_settings_menus()
        self.menu = [
            self.status_item,
            self.pause_item,
            None,
            self.cal_item,
            self.viewcam_item,
            self.profiles_menu,
            None,
            self.toggle_notif,
            self.toggle_sound,
            self.toggle_icon,
            self.toggle_snapshot,
            self.settings_menu,
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]
        self._sync_toggle_states()
        self._rebuild_profiles_menu()

        try:
            # dialogs (NSAlert/rumps.Window) show the app icon; a bare python
            # process has none, so they'd display a generic folder otherwise
            icon_image = AppKit.NSImage.alloc().initByReferencingFile_(str(LOGO_PATH))
            if icon_image is not None:
                AppKit.NSApplication.sharedApplication().setApplicationIconImage_(icon_image)
        except Exception:
            logger.exception("could not set app icon")

        self._splash_window: object | None = None
        self._splash_started = 0.0
        self._calibration_offered = False
        self._show_splash()

        self.detector.start()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._timer = rumps.Timer(self.on_tick, 1)
        self._timer.start()

    # ---------- splash ----------

    def _show_splash(self) -> None:
        try:
            if not LOGO_PATH.exists():
                return
            image = AppKit.NSImage.alloc().initByReferencingFile_(str(LOGO_PATH))
            if image is None:
                return
            size = 360.0
            screen = AppKit.NSScreen.mainScreen().frame()
            rect = Foundation.NSMakeRect(
                (screen.size.width - size) / 2,
                (screen.size.height - size) / 2,
                size,
                size,
            )
            window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                rect,
                AppKit.NSWindowStyleMaskBorderless,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            window.setOpaque_(False)
            window.setBackgroundColor_(AppKit.NSColor.clearColor())
            window.setLevel_(AppKit.NSFloatingWindowLevel)
            window.setHasShadow_(False)
            view = AppKit.NSImageView.alloc().initWithFrame_(
                Foundation.NSMakeRect(0, 0, size, size)
            )
            view.setImage_(image)
            view.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
            window.setContentView_(view)
            window.orderFrontRegardless()
            self._splash_window = window
            self._splash_started = time.time()
        except Exception:
            logger.exception("splash failed")

    def _close_splash(self) -> None:
        window = self._splash_window
        self._splash_window = None
        if window is not None:
            try:
                window.orderOut_(None)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("splash close failed")

    def _offer_calibration(self) -> None:
        self._calibration_offered = True
        self.status_item.title = "State: uncalibrated"
        self._set_title(ICONS[Posture.UNCALIBRATED])
        # menu bar apps don't activate on their own: without this the modal
        # dialog can open BEHIND other windows and invisibly block startup
        self._activate_app()
        try:
            response = rumps.alert(
                title="Slouch No More",
                message=(
                    "No calibration yet - I need to learn what your good "
                    "posture and your slouch look like (about 20 seconds, "
                    "with voice guidance)."
                ),
                ok="Calibrate now",
                cancel="Later",
                icon_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
            )
        except Exception:
            logger.exception("calibration offer dialog failed")
            return
        if response == 1:
            self.on_calibrate(None)

    # ---------- worker ----------

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if self.paused:
                self.detector.close_camera()
                self._stop.wait(0.5)
                continue
            # calibration/preview always needs a live stream
            live_needed = self._preview_proc is not None or self.capturing
            snapshot = self.config.capture_mode == "snapshot" and not live_needed
            try:
                if not self.detector.is_open:
                    self.detector.open_camera(warmup_frames=3)
                metrics = self.detector.read()
            except Exception:
                logger.exception("detector read failed")
                metrics = None
            with self._lock:
                self._samples.append((time.time(), metrics))
            self._pump_preview()
            if snapshot:
                self.detector.close_camera()
                interval = self.config.snapshot_interval
            else:
                # sample fast while the preview window is open, gently otherwise
                interval = 0.1 if self._preview_proc is not None else self.config.poll_interval
            if self._wake.wait(timeout=interval):
                self._wake.clear()

    def _pump_preview(self) -> None:
        proc = self._preview_proc
        if proc is None or proc.stdin is None:
            return
        sub_text: str | None = None
        sub_ok = False
        if self._gap_stats is not None:
            latest = self._latest()
            if latest is not None:
                diff = self._gap_stats.z_norm(latest.to_vector())
                target = self.config.separation_min
                sub_ok = diff >= target
                verdict = "enough!" if sub_ok else "slouch harder"
                sub_text = f"difference from GOOD: {diff:.1f} / need {target:.1f} - {verdict}"
        ghosts: list[tuple[dict[int, tuple[int, int]], tuple[int, int, int], str]] = []
        if self._ghost_points:
            ghosts.append((self._ghost_points, (255, 200, 60), "blue = your GOOD pose"))
        if self._viewcam:
            active = self.store.active
            size = self.detector.frame_size
            if active is not None and size is not None:
                w, h = size
                ghosts.append(
                    (self._pose_from_stats(active.good, w, h), (255, 200, 60), "blue = saved GOOD")
                )
                ghosts.append(
                    (self._pose_from_stats(active.slouch, w, h), (90, 90, 255), "red = saved SLOUCH")
                )
        data = self.detector.annotated_frame(
            self._preview_text, ghosts, sub_text, sub_ok, self._preview_border
        )
        if data is None:
            return
        try:
            proc.stdin.write(len(data).to_bytes(4, "big") + data)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._stop_preview()  # viewer was closed (Esc / window close)

    def _start_preview(self, text: str) -> None:
        self._preview_text = text
        if self._preview_proc is not None:
            return
        self._preview_proc = subprocess.Popen(
            [sys.executable, "-m", "posture_guard.preview"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        threading.Thread(
            target=self._preview_stdout_reader, args=(self._preview_proc,), daemon=True
        ).start()
        logger.info("preview window opened")

    def _preview_stdout_reader(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                if b"SKIP" in line:
                    self._skip_intro.set()
        except (OSError, ValueError):
            pass

    def _stop_preview(self) -> None:
        proc = self._preview_proc
        self._preview_proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        logger.info("preview window closed")

    def _recent_metrics(self, window: float, now: float) -> list[PostureMetrics]:
        with self._lock:
            return [m for ts, m in self._samples if m is not None and now - ts <= window]

    def _latest(self) -> PostureMetrics | None:
        with self._lock:
            if not self._samples:
                return None
            return self._samples[-1][1]

    # ---------- main loop ----------

    def on_tick(self, _timer: rumps.Timer) -> None:
        if self._splash_window is not None:
            if time.time() - self._splash_started < SPLASH_SECONDS:
                return
            self._close_splash()
            try:
                # re-assert once the run loop is live: set at __init__ it can
                # be reset during app activation
                icon_image = AppKit.NSImage.alloc().initByReferencingFile_(str(LOGO_PATH))
                if icon_image is not None:
                    AppKit.NSApplication.sharedApplication().setApplicationIconImage_(icon_image)
            except Exception:
                logger.exception("could not set app icon")
            if self.store.active is None and not self._calibration_offered:
                self._offer_calibration()
        if self._viewcam and self._preview_proc is None and not self.capturing:
            self._viewcam = False  # viewer window was closed (Esc)
            self.viewcam_item.state = 0
        if self.paused or self.capturing:
            return
        now = time.time()
        # classify on a 3s median so single-frame noise can't flap the state
        latest = median_metrics(self._recent_metrics(window=3.0, now=now)) or self._latest()
        posture = classify(latest, self.store.active, self.config.unknown_threshold)
        if posture is not self._last_logged:
            active = self.store.active
            if latest is not None and active is not None:
                t, residual = active.project(latest)
                logger.info(
                    "state -> %s (t=%.2f residual=%.2f)", posture.value, t, residual
                )
            else:
                logger.info("state -> %s", posture.value)
            self._last_logged = posture
        events = self.supervisor.tick(now, posture)
        self._set_title(ICONS[posture])
        active = self.store.active
        self.status_item.title = (
            f"State: {posture.value}"
            + (f"  |  profile: {active.name}" if active else "  |  no profile")
        )
        for event in events:
            self._handle_event(event, now)

    def _handle_event(self, event: Event, now: float) -> None:
        logger.info("event: %s", event.value)
        if event is Event.SLOUCH_ALERT:
            self.alerter.slouch()
        elif event is Event.BACK_TO_GOOD:
            if self.config.back_to_good_chime and self.config.alert_sound:
                play_sound(self.config.sound_info)
        elif event is Event.CAMERA_MOVED:
            recent = self._recent_metrics(window=10.0, now=now)
            match = self.store.best_match(recent, self.config.match_threshold) if recent else None
            if match is not None and match.profile_id != self.store.active_id:
                self.store.activate(match.profile_id)
                self.supervisor.reset(now)
                self._rebuild_profiles_menu()
                self.alerter.info(f"Camera angle changed - switched to profile '{match.name}'.")
                logger.info("auto-switched to profile %s", match.name)
            else:
                self.alerter.info(
                    "Camera seems to have moved and no saved profile matches - "
                    "recalibrate from the menu.",
                    sound=True,
                )

    def _set_title(self, icon: str) -> None:
        self.title = icon if self.config.alert_icon else "PG"

    @staticmethod
    def _pose_from_stats(stats: FeatureStats, w: int, h: int) -> dict[int, tuple[int, int]]:
        """Approximate nose + shoulder pixel positions from a saved cluster's
        feature means (profiles store features, not pixels; shoulder-mid x is
        assumed under the nose, which holds for a roughly frontal camera)."""
        mid_x, nose_y, mid_y, width, _head_drop, tilt_scaled = stats.mean
        tilt = math.radians(tilt_scaled * TILT_SCALE)
        half_dx = (width / 2) * math.cos(tilt)
        half_dy = (width / 2) * math.sin(tilt)
        return {
            NOSE: (int(mid_x * w), int(nose_y * h)),
            LEFT_SHOULDER: (int((mid_x + half_dx) * w), int((mid_y + half_dy) * h)),
            RIGHT_SHOULDER: (int((mid_x - half_dx) * w), int((mid_y - half_dy) * h)),
        }

    def on_toggle_viewcam(self, _s: rumps.MenuItem) -> None:
        if self._viewcam:
            self._viewcam = False
            if not self.capturing:
                self._stop_preview()
        else:
            self._viewcam = True
            self._wake.set()
            if not self.capturing:
                self._preview_text = "Live view (Esc or menu to close)"
                self._preview_border = None
                self._start_preview(self._preview_text)
        self.viewcam_item.state = 1 if self._viewcam else 0

    # ---------- calibration ----------

    def on_calibrate(self, _sender: rumps.MenuItem) -> None:
        if self.capturing:
            return
        self.capturing = True
        self._wake.set()  # worker may be mid-sleep in snapshot mode
        threading.Thread(target=self._calibrate_worker, daemon=True).start()

    def _say(self, text: str, blocking: bool = False, key: str | None = None) -> None:
        if self.config.voice_guidance:
            speak(text, blocking=blocking, phrase_key=key)

    def _capture_phase(self, kind: str, hint: str) -> list[PostureMetrics]:
        for remaining in range(int(CAPTURE_COUNTDOWN), 0, -1):
            self._preview_text = f"{hint} - capturing in {remaining}..."
            tick_start = time.time()
            self._say(str(remaining), blocking=True, key=str(remaining))
            time.sleep(max(0.0, 1.0 - (time.time() - tick_start)))
        self._preview_text = f"CAPTURING {kind} - hold it!"
        self._say("Hold it.", key="hold")
        time.sleep(1.0)  # settle: don't let getting-into-position frames into the stats
        start = time.time()
        time.sleep(CAPTURE_WINDOW)
        samples = self._recent_metrics(window=time.time() - start, now=time.time())
        logger.info("capture %s collected %d samples", kind, len(samples))
        return samples

    def _calibrate_worker(self) -> None:
        logger.info("guided calibration started")
        try:
            self._preview_border = (80, 220, 80)  # green = GOOD phase
            self._start_preview("Step 1 of 2 - your GOOD posture")
            self._skip_intro.clear()
            if self.config.voice_guidance and not self.config.skip_calibration_intro:
                self._preview_text = "Step 1 of 2 - GOOD posture  (S = skip intro)"
                skipped = speak_cancellable(
                    "Welcome! We're going to calibrate your posture. First I'll "
                    "capture your good posture, then your slouch. The whole "
                    "thing takes about twenty seconds.",
                    self._skip_intro,
                    phrase_key="welcome",
                ) or speak_cancellable(
                    "Step one: sit tall, in your best posture.",
                    self._skip_intro,
                    phrase_key="intro",
                )
                if skipped:
                    self.config.skip_calibration_intro = True
                    self.config.save()
                    self._build_settings_menus()
                    logger.info("intro skipped; skip_calibration_intro saved")
            good = self._capture_phase(
                "GOOD", "Step 1/2: sit TALL - back straight, chin up"
            )
            if len(good) < 4:
                self._preview_text = "FAILED - I couldn't see you. Try again."
                self._say("I couldn't see you. Try again.", key="not_seen")
                self.alerter.info("Not enough pose samples - make sure you're in frame.", sound=True)
                time.sleep(3)
                return
            # freeze the GOOD pose on screen and show a live difference readout
            self._ghost_points = self.detector.last_points or None
            self._gap_stats = FeatureStats.from_samples(good)
            self._preview_border = (60, 160, 255)  # orange = SLOUCH phase
            self._preview_text = "Step 2 of 2 - now your WORST slouch"
            self._say(
                "Great. Step two: now slouch. Chin down, shoulders forward. "
                "Make it dramatic.",
                blocking=True,
                key="step_two",
            )
            time.sleep(1.0)
            slouch = self._capture_phase(
                "SLOUCH", "Step 2/2: collapse - chin down, shoulders forward"
            )
            if len(slouch) < 4:
                self._preview_text = "FAILED - I couldn't see you. Try again."
                self._say("I couldn't see you. Try again.", key="not_seen")
                self.alerter.info("Not enough pose samples - make sure you're in frame.", sound=True)
                time.sleep(3)
                return
            name = datetime.now().strftime("angle %b %d %H:%M")
            profile = new_profile(name, good, slouch)
            separation = profile.separation()
            gaps = ", ".join(
                f"{fname}={abs(g - s) / max(sg, ss):.1f}"
                for fname, g, s, sg, ss in zip(
                    FEATURE_NAMES,
                    profile.good.mean,
                    profile.slouch.mean,
                    profile.good.std,
                    profile.slouch.std,
                )
            )
            logger.info(
                "profile created: %s (separation=%.2f; per-feature z-gaps: %s)",
                name, separation, gaps,
            )
            if separation < self.config.separation_min:
                # clusters overlap: classification would flap, alerts never fire
                self._preview_text = "TOO SIMILAR - run it again, exaggerate the slouch!"
                self._say(
                    "Too similar. Run it again, and really slouch this time.",
                    key="too_similar",
                )
                self.alerter.info(
                    "Your GOOD and SLOUCH postures look almost the same to the "
                    "camera - run calibration again and really slouch.",
                    sound=True,
                )
                time.sleep(4)
                return
            self.store.add(profile, activate=True)
            self.supervisor.reset(time.time())
            self._rebuild_profiles_menu()
            self._preview_text = f"Saved '{name}' - watching your posture now."
            self._say("Calibration saved. I'm watching your posture now.", key="saved")
            self.alerter.info(f"Calibration saved as '{name}' - watching your posture now.", sound=True)
            time.sleep(2.5)
        finally:
            self._ghost_points = None
            self._stop_preview()
            self.capturing = False

    # ---------- menu callbacks ----------

    def _rebuild_profiles_menu(self) -> None:
        # rumps creates a MenuItem's underlying NSMenu lazily on first add();
        # clear() before that crashes on None
        if len(self.profiles_menu):
            self.profiles_menu.clear()
        if not self.store.profiles:
            item = rumps.MenuItem("(none saved)")
            self.profiles_menu.add(item)
            return
        for p in self.store.profiles:
            item = rumps.MenuItem(p.name, callback=self._on_profile_selected)
            item.state = 1 if p.profile_id == self.store.active_id else 0
            item.profile_id = p.profile_id  # type: ignore[attr-defined]
            self.profiles_menu.add(item)
        self.profiles_menu.add(
            rumps.MenuItem("Clear all profiles...", callback=self.on_clear_profiles)
        )

    def _on_profile_selected(self, sender: rumps.MenuItem) -> None:
        self.store.activate(sender.profile_id)  # type: ignore[attr-defined]
        self.supervisor.reset(time.time())
        self._rebuild_profiles_menu()

    @staticmethod
    def _activate_app() -> None:
        """Bring the app frontmost so modal dialogs can't open hidden."""
        try:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            logger.exception("could not activate app")

    def on_clear_profiles(self, _s: rumps.MenuItem) -> None:
        if not self.store.profiles:
            return
        self._activate_app()
        response = rumps.alert(
            title="Clear all profiles?",
            message=(
                f"This deletes all {len(self.store.profiles)} saved calibration "
                "profile(s). You'll need to calibrate again."
            ),
            ok="Clear",
            cancel="Cancel",
            icon_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
        )
        if response != 1:
            return
        self.store.clear_all()
        self.supervisor.reset(time.time())
        self._rebuild_profiles_menu()
        logger.info("all profiles cleared")

    def on_pause(self, sender: rumps.MenuItem) -> None:
        self.paused = not self.paused
        sender.title = "Resume" if self.paused else "Pause"
        if self.paused:
            self.title = ICON_PAUSED

    def on_toggle_notif(self, _s: rumps.MenuItem) -> None:
        self.config.alert_notification = not self.config.alert_notification
        self._save_and_sync()

    def on_toggle_sound(self, _s: rumps.MenuItem) -> None:
        self.config.alert_sound = not self.config.alert_sound
        self._save_and_sync()

    def on_toggle_icon(self, _s: rumps.MenuItem) -> None:
        self.config.alert_icon = not self.config.alert_icon
        self._save_and_sync()

    @staticmethod
    def _reset_submenu(menu: rumps.MenuItem) -> None:
        # rumps builds a MenuItem's NSMenu lazily; clear() before first add crashes
        if len(menu):
            menu.clear()

    def _build_settings_menus(self) -> None:
        self._reset_submenu(self.sound_bad_menu)
        self._reset_submenu(self.sound_good_menu)
        self._reset_submenu(self.delay_menu)
        self._reset_submenu(self.repeat_menu)
        sounds = sorted(SYSTEM_SOUNDS_DIR.glob("*.aiff"))
        for menu, current, kind in (
            (self.sound_bad_menu, self.config.sound_slouch, "bad"),
            (self.sound_good_menu, self.config.sound_info, "good"),
        ):
            for path in sounds:
                item = rumps.MenuItem(path.stem, callback=self._on_pick_sound)
                item.state = 1 if str(path) == current else 0
                item.sound_path = str(path)  # type: ignore[attr-defined]
                item.sound_kind = kind  # type: ignore[attr-defined]
                menu.add(item)
        for seconds in ALERT_DELAY_CHOICES:
            item = rumps.MenuItem(f"{seconds} seconds", callback=self._on_pick_delay)
            item.state = 1 if seconds == int(self.config.slouch_alert_seconds) else 0
            item.delay_seconds = seconds  # type: ignore[attr-defined]
            self.delay_menu.add(item)
        for seconds in ALERT_REPEAT_CHOICES:
            label = "Just once" if seconds == 0 else f"Every {seconds} seconds"
            item = rumps.MenuItem(label, callback=self._on_pick_repeat)
            item.state = 1 if seconds == int(self.config.alert_repeat_seconds) else 0
            item.repeat_seconds = seconds  # type: ignore[attr-defined]
            self.repeat_menu.add(item)
        self.login_item.state = 1 if LAUNCH_AGENT_PATH.exists() else 0
        self.voice_item.state = 1 if self.config.voice_guidance else 0
        self._reset_submenu(self.advanced_menu)
        for f in fields(Config):
            value = getattr(self.config, f.name)
            item = rumps.MenuItem(f"{f.name} = {value}", callback=self._on_edit_config_var)
            item.field_name = f.name  # type: ignore[attr-defined]
            self.advanced_menu.add(item)
        self.intro_item.state = 0 if self.config.skip_calibration_intro else 1

    def _on_pick_sound(self, sender: rumps.MenuItem) -> None:
        if sender.sound_kind == "bad":  # type: ignore[attr-defined]
            self.config.sound_slouch = sender.sound_path  # type: ignore[attr-defined]
        else:
            self.config.sound_info = sender.sound_path  # type: ignore[attr-defined]
        self.config.save()
        play_sound(sender.sound_path)  # type: ignore[attr-defined]
        self._build_settings_menus()

    def _on_pick_delay(self, sender: rumps.MenuItem) -> None:
        seconds = float(sender.delay_seconds)  # type: ignore[attr-defined]
        self.config.slouch_alert_seconds = seconds
        self.supervisor.slouch_alert_seconds = seconds
        self.config.save()
        logger.info("slouch alert delay -> %ss", seconds)
        self._build_settings_menus()

    def _on_pick_repeat(self, sender: rumps.MenuItem) -> None:
        seconds = float(sender.repeat_seconds)  # type: ignore[attr-defined]
        self.config.alert_repeat_seconds = seconds
        self.supervisor.alert_repeat_seconds = seconds
        self.config.save()
        logger.info("alert repeat -> %s", "once" if seconds == 0 else f"{seconds}s")
        self._build_settings_menus()

    def on_toggle_voice(self, _s: rumps.MenuItem) -> None:
        self.config.voice_guidance = not self.config.voice_guidance
        self.config.save()
        self._build_settings_menus()

    def on_toggle_intro(self, _s: rumps.MenuItem) -> None:
        self.config.skip_calibration_intro = not self.config.skip_calibration_intro
        self.config.save()
        self._build_settings_menus()

    def on_toggle_login(self, _s: rumps.MenuItem) -> None:
        if LAUNCH_AGENT_PATH.exists():
            subprocess.run(
                ["launchctl", "unload", str(LAUNCH_AGENT_PATH)], capture_output=True
            )
            LAUNCH_AGENT_PATH.unlink()
            logger.info("launch agent removed")
        else:
            binary = str(Path(sys.executable).parent / "posture-guard")
            LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENT_PATH.write_text(LAUNCH_AGENT_PLIST.format(binary=binary))
            # not launchctl-loaded now: RunAtLoad would start a duplicate
            # instance immediately; the agent takes effect at next login
            logger.info("launch agent installed (active from next login): %s", binary)
        self._build_settings_menus()

    def _on_edit_config_var(self, sender: rumps.MenuItem) -> None:
        name = sender.field_name  # type: ignore[attr-defined]
        current = getattr(self.config, name)
        self._activate_app()
        window = rumps.Window(
            message=f"{name} (current: {current!r})",
            title="Edit setting",
            default_text=str(current),
            ok="Save",
            cancel="Cancel",
        )
        response = window.run()
        if response.clicked != 1:
            return
        raw = response.text.strip()
        try:
            value: object
            if isinstance(current, bool):
                if raw.lower() not in ("true", "false", "1", "0", "yes", "no"):
                    raise ValueError(raw)
                value = raw.lower() in ("true", "1", "yes")
            elif isinstance(current, float):
                value = float(raw)
            elif isinstance(current, int):
                value = int(raw)
            else:
                value = raw
        except ValueError:
            self.alerter.info(f"'{raw}' is not a valid value for {name} - unchanged.")
            return
        setattr(self.config, name, value)
        self.config.save()
        logger.info("config %s -> %r", name, value)
        self._apply_config()

    def _apply_config(self) -> None:
        """Re-apply the live config to everything that caches pieces of it."""
        self.alerter.config = self.config
        self.supervisor.slouch_alert_seconds = self.config.slouch_alert_seconds
        self.supervisor.alert_repeat_seconds = self.config.alert_repeat_seconds
        self.supervisor.camera_move_seconds = self.config.camera_move_seconds
        self.toggle_snapshot.title = (
            f"Snapshot mode (camera opens every {self.config.snapshot_interval:.0f}s)"
        )
        self._sync_toggle_states()
        self._build_settings_menus()

    def on_open_config(self, _s: rumps.MenuItem) -> None:
        if not DEFAULT_CONFIG_PATH.exists():
            self.config.save()
        subprocess.Popen(["open", str(DEFAULT_CONFIG_PATH)])

    def on_reload_config(self, _s: rumps.MenuItem) -> None:
        self.config = Config.load()
        self.supervisor.reset(time.time())
        self._apply_config()
        logger.info("config reloaded")
        self.alerter.info("Config reloaded.")

    def on_toggle_snapshot(self, _s: rumps.MenuItem) -> None:
        snapshot = self.config.capture_mode != "snapshot"
        self.config.capture_mode = "snapshot" if snapshot else "continuous"
        logger.info("capture mode -> %s", self.config.capture_mode)
        self._save_and_sync()

    def _save_and_sync(self) -> None:
        self.config.save()
        self._sync_toggle_states()

    def _sync_toggle_states(self) -> None:
        self.toggle_notif.state = 1 if self.config.alert_notification else 0
        self.toggle_sound.state = 1 if self.config.alert_sound else 0
        self.toggle_icon.state = 1 if self.config.alert_icon else 0
        self.toggle_snapshot.state = 1 if self.config.capture_mode == "snapshot" else 0

    def on_quit(self, _sender: rumps.MenuItem) -> None:
        self._stop.set()
        self._wake.set()
        self._stop_preview()
        self.detector.stop()
        rumps.quit_application()


def main() -> None:
    PostureGuardApp().run()
