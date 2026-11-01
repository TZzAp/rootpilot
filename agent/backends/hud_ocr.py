"""Text recognition through the SpringBoard overlay (hud/), instead of ZXTouch's OCR.

ZXTouch's OCR leaks 15-30 MB of SpringBoard memory per read (measured on iOS 15 and 16), so
long runs end with jetsam killing SpringBoard. The overlay runs Apple's Vision text recognition
inside an autorelease pool and frees the screen image after each read.

The same handshake also carries clipboard requests (HudClipboard) and raw screen pixels
(HudPixels). Requests use the same
private-folder handshake as screen capture: RootPilot writes
``ocr.request`` (with an id) into its 0700 folder and posts ``com.rootpilot.hud.ocr``; the
overlay answers in ``ocr.result.json`` with the same id. Coordinates come back in points.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from agent.errors import BackendError, BackendTimeout
from agent.indicator import NOTIFY_PREFIX, _notify_poster
from agent.paths import ensure_private_dir, hud_dir
from agent.perception.png import RGBImage

Box = tuple[str, float, float, float, float]


def hud_request(
    kind: str,
    payload: dict[str, Any],
    *,
    folder: Optional[Path] = None,
    timeout: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    notify: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """One request/response with the overlay: ``<kind>.request`` in, ``<kind>.result.json`` out."""
    folder = ensure_private_dir(folder or hud_dir())
    result = folder / f"{kind}.result.json"
    rid = uuid.uuid4().hex
    tmp = folder / f"{kind}.request.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({**payload, "id": rid}, fh)
    os.replace(tmp, folder / f"{kind}.request")
    (notify or _notify_poster())(f"{NOTIFY_PREFIX}.{kind}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data: dict[str, Any] = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("id") == rid:
            # Clipboard results can hold user text: don't leave them lying around.
            result.unlink(missing_ok=True)
            return data
        sleep(0.05)
    raise BackendTimeout(
        f"overlay did not answer the {kind} request; is it loaded? (rootpilot doctor)"
    )


class HudOCR:
    def __init__(
        self,
        folder: Optional[Path] = None,
        timeout: float = 15.0,
        fast: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        notify: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._kw: dict[str, Any] = {
            "folder": folder,
            "timeout": timeout,
            "sleep": sleep,
            "notify": notify,
        }
        self._fast = fast

    def read(self, roi: Optional[tuple[float, float, float, float]] = None) -> list[Box]:
        """Text boxes in points; ``roi`` (x, y, w, h in points) reads only that part (faster)."""
        payload: dict[str, Any] = {"fast": self._fast}
        if roi is not None:
            payload["roi"] = list(roi)
        data = hud_request("ocr", payload, **self._kw)
        if data.get("error"):
            raise BackendError(f"overlay OCR failed: {data['error']}")
        return [
            (str(i["text"]), float(i["x"]), float(i["y"]), float(i["w"]), float(i["h"]))
            for i in data.get("items", [])
        ]


class HudClipboard:
    """The system clipboard, through the overlay (ZXTouch has no working clipboard)."""

    def __init__(self, **kw: Any) -> None:
        self._kw = kw

    def set(self, text: str) -> None:
        data = hud_request("clipboard", {"op": "set", "text": text}, **self._kw)
        if not data.get("ok"):
            raise BackendError(f"clipboard: {data.get('error', 'failed')}")

    def get(self) -> str:
        data = hud_request("clipboard", {"op": "get"}, **self._kw)
        if not data.get("ok"):
            raise BackendError(f"clipboard: {data.get('error', 'failed')}")
        return str(data.get("text", ""))


class HudPixels:
    """The screen as RGB, one pixel per point, for boards of coloured blocks OCR can't see."""

    def __init__(self, folder: Optional[Path] = None, **kw: Any) -> None:
        self._folder = folder
        self._kw = kw

    def read(self, scale: float = 1.0) -> RGBImage:
        """``scale`` pixels per point (1-3); the overlay resizes, which Python can't do quickly."""
        folder = self._folder or hud_dir()
        data = hud_request("pixels", {"scale": scale}, folder=folder, **self._kw)
        # The overlay names the file, but read it from our own private folder regardless.
        path = folder / "pixels.rgb"
        try:
            if not data.get("ok"):
                raise BackendError(f"overlay pixels: {data.get('error') or 'failed'}")
            width, height = int(data["w"]), int(data["h"])
            raw = path.read_bytes()
        except (OSError, KeyError, ValueError) as exc:
            raise BackendError(f"overlay pixels: {exc}") from exc
        finally:
            path.unlink(missing_ok=True)  # it is a picture of the screen
        if len(raw) != width * height * 3:
            raise BackendError(f"overlay pixels: expected {width}x{height}, got {len(raw)} bytes")
        return RGBImage(width, height, raw)

    def jpeg(self, scale: float = 1.0, quality: float = 0.7) -> tuple[bytes, int, int]:
        """A JPEG screenshot (bytes, width, height), encoded by the overlay."""
        folder = self._folder or hud_dir()
        payload = {"scale": scale, "format": "jpeg", "quality": quality}
        data = hud_request("pixels", payload, folder=folder, **self._kw)
        path = folder / "pixels.jpg"
        try:
            if not data.get("ok"):
                raise BackendError(f"overlay screenshot: {data.get('error') or 'failed'}")
            return path.read_bytes(), int(data["w"]), int(data["h"])
        except (OSError, KeyError, ValueError) as exc:
            raise BackendError(f"overlay screenshot: {exc}") from exc
        finally:
            path.unlink(missing_ok=True)
