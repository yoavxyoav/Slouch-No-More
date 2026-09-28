"""Alert channels: macOS notification and sound. The icon channel is the
menu bar title, handled by the app itself."""

from __future__ import annotations

import logging
import subprocess

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
