"""Alert channels: macOS notification and sound. The icon channel is the
menu bar title, handled by the app itself."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from posture_guard.config import Config

logger = logging.getLogger("posture_guard.alerts")


def notify(title: str, message: str) -> None:
    script = 'display notification "{}" with title "{}"'.format(
        message.replace('"', "'"), title.replace('"', "'")
    )
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.exception("failed to send notification")


VOICE_DIR = Path(__file__).resolve().parents[2] / "assets" / "voice"


def speak(text: str, blocking: bool = False, phrase_key: str | None = None) -> None:
    """Speak a calibration phrase. Pre-generated Kokoro TTS clips (bundled in
    assets/voice, keyed by `phrase_key`) sound far better than macOS `say`,
    which remains the fallback when a clip is missing. Blocking keeps
    sequential phrases from talking over each other (the countdown)."""
    clip = VOICE_DIR / f"{phrase_key}.mp3" if phrase_key else None
    cmd = ["afplay", str(clip)] if clip is not None and clip.exists() else ["say", text]
    try:
        if blocking:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        logger.exception("failed to speak")


def speak_cancellable(
    text: str, cancel: threading.Event, phrase_key: str | None = None
) -> bool:
    """Blocking speech that stops mid-sentence when `cancel` is set.
    Returns True if it was cancelled."""
    clip = VOICE_DIR / f"{phrase_key}.mp3" if phrase_key else None
    cmd = ["afplay", str(clip)] if clip is not None and clip.exists() else ["say", text]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        logger.exception("failed to speak")
        return cancel.is_set()
    while proc.poll() is None:
        if cancel.is_set():
            proc.terminate()
            return True
        time.sleep(0.05)
    return cancel.is_set()


def play_sound(path: str) -> None:
    try:
        subprocess.Popen(
            ["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        logger.exception("failed to play sound")


class Alerter:
    def __init__(self, config: Config) -> None:
        self.config = config

    def slouch(self) -> None:
        if self.config.alert_notification:
            notify("Posture Guard", "You're slouching - sit up straight.")
        if self.config.alert_sound:
            play_sound(self.config.sound_slouch)

    def info(self, message: str, sound: bool = False) -> None:
        if self.config.alert_notification:
            notify("Posture Guard", message)
        if sound and self.config.alert_sound:
            play_sound(self.config.sound_info)
