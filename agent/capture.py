"""``rootpilot capture``: save screen frames through the SpringBoard overlay (hud/).

Used for docs and debugging. The overlay only captures when this file-based request exists in
RootPilot's private (0700) folder, so another app posting the notification can't trigger it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from agent.errors import RootPilotError
from agent.indicator import NOTIFY_PREFIX, _notify_poster, hud_installed
from agent.paths import ensure_private_dir, hud_dir


def request_capture(
    frames: int = 1,
    interval: float = 0.25,
    name: str = "capture",
    width: int = 540,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    if not hud_installed():
        raise RootPilotError("screen capture needs the edge-glow overlay (see hud/README.md)")
    folder = ensure_private_dir(hud_dir())
    status = folder / "capture.status.json"
    status.unlink(missing_ok=True)
    request = {"frames": frames, "interval": interval, "name": name, "width": width}
    tmp = folder / "capture.request.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(request, fh)
    os.replace(tmp, folder / "capture.request")
    _notify_poster()(f"{NOTIFY_PREFIX}.capture")

    deadline = time.monotonic() + frames * interval + 30
    while time.monotonic() < deadline:
        try:
            state = json.loads(status.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("error"):
            raise RootPilotError(f"capture failed: {state['error']}")
        if state.get("finished"):
            return Path(state["dir"])
        sleep(0.2)
    raise RootPilotError("capture timed out; is the overlay loaded? (rootpilot doctor)")
