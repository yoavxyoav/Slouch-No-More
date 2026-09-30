"""One-window editor for the config values the menu does not expose.

An NSAlert whose accessory view is a form: checkboxes for booleans, popups
for fields with a fixed set of choices (capture mode, sounds), text fields
for numbers. Invalid entries are reported inline and the form is re-shown
with the user's text intact until everything validates or they cancel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import AppKit
import Foundation

from posture_guard.config import (
    ADVANCED_FIELDS,
    CAPTURE_MODES,
    NUMERIC_LIMITS,
    Config,
    field_types,
    parse_settings,
)

LABEL_WIDTH = 240
CONTROL_WIDTH = 220
ROW_HEIGHT = 26
COLUMN_GAP = 8

NS_ALERT_FIRST_BUTTON = 1000  # NSAlertFirstButtonReturn
NS_SWITCH_BUTTON = 3  # NSButtonTypeSwitch (checkbox)

BASE_TEXT = "camera_index takes effect after a restart."


class AdvancedSettingsDialog:
    def __init__(self, config: Config, icon_path: Path | None, sounds: list[Path]) -> None:
        self.config = config
        self.icon_path = icon_path
        self.sounds = sounds
        self._types = field_types()
        self._controls: dict[str, Any] = {}
        self._popup_choices: dict[str, list[str]] = {}

    def run(self) -> dict[str, Any] | None:
        """Show the form until the user saves valid values or cancels.

        Returns {field: value} for every advanced field on success, None on cancel.
        """
        state: dict[str, Any] = {}
        for name in ADVANCED_FIELDS:
            value = getattr(self.config, name)
            state[name] = value if isinstance(value, (bool, str)) else f"{value:g}"
        errors: dict[str, str] = {}
        while True:
            alert = self._make_alert(state, errors)
            if alert.runModal() != NS_ALERT_FIRST_BUTTON:
                return None
            state = self._read_form()
            values, errors = parse_settings(state)
            if not errors:
                return values

    # ---------- building ----------

    def _make_alert(self, state: dict[str, Any], errors: dict[str, str]) -> Any:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Advanced settings")
        text = BASE_TEXT
        if errors:
            problems = "\n".join(f"{name}: {problem}" for name, problem in errors.items())
            text = f"Please fix:\n{problems}\n\n{BASE_TEXT}"
        alert.setInformativeText_(text)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        if self.icon_path is not None:
            image = AppKit.NSImage.alloc().initByReferencingFile_(str(self.icon_path))
            if image is not None:
                alert.setIcon_(image)
        alert.setAccessoryView_(self._build_form(state, errors))
        return alert

    def _build_form(self, state: dict[str, Any], errors: dict[str, str]) -> Any:
        names = list(ADVANCED_FIELDS)
        height = ROW_HEIGHT * len(names)
        width = LABEL_WIDTH + COLUMN_GAP + CONTROL_WIDTH
        view = AppKit.NSView.alloc().initWithFrame_(Foundation.NSMakeRect(0, 0, width, height))
        self._controls = {}
        self._popup_choices = {}
        for row, name in enumerate(names):
            # NSView is not flipped: y grows upward, so row 0 sits at the top
            y = height - (row + 1) * ROW_HEIGHT
            label = AppKit.NSTextField.labelWithString_(self._label_for(name))
            label.setFrame_(Foundation.NSMakeRect(0, y + 3, LABEL_WIDTH, ROW_HEIGHT - 6))
            label.setAlignment_(AppKit.NSTextAlignmentRight)
            if name in errors:
                label.setTextColor_(AppKit.NSColor.systemRedColor())
            view.addSubview_(label)
            control = self._make_control(name, state[name], y)
            self._controls[name] = control
            view.addSubview_(control)
        return view

    def _label_for(self, name: str) -> str:
        if name in NUMERIC_LIMITS:
            lo, hi = NUMERIC_LIMITS[name]
            return f"{name} ({lo:g} to {hi:g})"
        return name

    def _make_control(self, name: str, value: Any, y: float) -> Any:
        frame = Foundation.NSMakeRect(LABEL_WIDTH + COLUMN_GAP, y + 2, CONTROL_WIDTH, ROW_HEIGHT - 4)
        expected = self._types[name]
        if expected is bool:
            box = AppKit.NSButton.alloc().initWithFrame_(frame)
            box.setButtonType_(NS_SWITCH_BUTTON)
            box.setTitle_("")
            box.setState_(1 if value else 0)
            return box
        choices = self._choices_for(name, value)
        if choices is not None:
            popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(frame, False)
            popup.addItemsWithTitles_([self._choice_title(name, c) for c in choices])
            popup.selectItemAtIndex_(choices.index(value))
            self._popup_choices[name] = choices
            return popup
        field = AppKit.NSTextField.alloc().initWithFrame_(frame)
        field.setStringValue_(str(value))
        return field

    def _choices_for(self, name: str, current: str) -> list[str] | None:
        choices: list[str]
        if name == "capture_mode":
            choices = list(CAPTURE_MODES)
        elif name.startswith("sound_"):
            choices = [str(p) for p in self.sounds]
        else:
            return None
        if current not in choices:
            # keep a custom/unknown value selectable instead of silently dropping it
            choices.append(current)
        return choices

    @staticmethod
    def _choice_title(name: str, choice: str) -> str:
        return Path(choice).stem if name.startswith("sound_") else choice

    # ---------- reading ----------

    def _read_form(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for name, control in self._controls.items():
            if self._types[name] is bool:
                state[name] = bool(control.state())
            elif name in self._popup_choices:
                state[name] = self._popup_choices[name][control.indexOfSelectedItem()]
            else:
                state[name] = str(control.stringValue())
        return state
