"""Calibration preview window.

Runs as a separate process because the menu bar app's main thread belongs to
the Cocoa event loop, and macOS GUI windows (cv2.imshow included) must run on
a process's main thread. Frames arrive length-prefixed on stdin; the window
closes when the pipe closes. Nothing is written to disk.
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

WINDOW = "Posture Guard - calibration"


def main() -> None:
    stdin = sys.stdin.buffer
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            header = stdin.read(4)
            if len(header) < 4:
                break
            size = int.from_bytes(header, "big")
            data = stdin.read(size)
            if len(data) < size:
                break
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            cv2.imshow(WINDOW, frame)
            if cv2.waitKey(1) & 0xFF == 27:  # Esc closes early
                break
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
