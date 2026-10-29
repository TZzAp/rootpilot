"""Text recognition through the SpringBoard overlay (hud/), instead of ZXTouch's OCR.

ZXTouch's OCR leaks 15-30 MB of SpringBoard memory per read (measured on iOS 15 and 16), so
long runs end with jetsam killing SpringBoard. The overlay runs Apple's Vision text recognition
inside an autorelease pool and frees the screen image after each read.

Requests use the same private-folder handshake as screen capture: RootPilot writes
``ocr.request`` (with an id) into its 0700 folder and posts ``com.rootpilot.hud.ocr``; the
overlay answers in ``ocr.result.json`` with the same id. Coordinates come back in points.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from agent.errors import BackendError, BackendTimeout
from agent.indicator import NOTIFY_PREFIX, _notify_poster
from agent.paths import ensure_private_dir, hud_dir

Box = tuple[str, float, float, float, float]


class HudOCR:
    def __init__(
        self,
        folder: Optional[Path] = None,
        timeout: float = 15.0,
        fast: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        notify: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._folder = folder
        self._timeout = timeout
        self._fast = fast
        self._sleep = sleep
        self._notify = notify or _notify_poster()

    def read(self) -> list[Box]:
        folder = ensure_private_dir(self._folder or hud_dir())
        result = folder / "ocr.result.json"
        rid = uuid.uuid4().hex
        tmp = folder / "ocr.request.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"id": rid, "fast": self._fast}, fh)
        os.replace(tmp, folder / "ocr.request")
        self._notify(f"{NOTIFY_PREFIX}.ocr")

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                data = json.loads(result.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if data.get("id") == rid:
                if data.get("error"):
                    raise BackendError(f"overlay OCR failed: {data['error']}")
                return [
                    (str(i["text"]), float(i["x"]), float(i["y"]), float(i["w"]), float(i["h"]))
                    for i in data.get("items", [])
                ]
            self._sleep(0.05)
        raise BackendTimeout("overlay OCR did not answer; is it loaded? (rootpilot doctor)")
