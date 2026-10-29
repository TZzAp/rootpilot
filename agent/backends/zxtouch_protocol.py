"""ZXTouch socket wire format.

Taken from the upstream Python client (xuan32546/IOS13-SimulateTouch,
``layout/usr/lib/python3.7/site-packages/zxtouch``). Upstream targets iOS 11–14; touch, swipe,
app launch, screen size/scale and OCR were verified on an iPhone X running iOS 16.7.11
(palera1n rootful, ZXTouch 0.0.7-10 rebound to loopback) on 2026-10-29. Text input is
untested on iOS 16.

Request:  ``<task code><field>;;<field>;;...\\r\\n`` (no separator after the task code)
Response: ``0;;<field>;;...\\r\\n`` on success, anything else (``<code>;;<error>``) on failure.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

TASK_PERFORM_TOUCH = 10
TASK_PROCESS_BRING_FOREGROUND = 11
TASK_KEYBOARD = 24
TASK_GET_DEVICE_INFO = 25
TASK_SHOW_TOAST = 22
TASK_TEXT_RECOGNIZER = 27
# TASK_RUN_SHELL (13) exists upstream and runs commands as root. RootPilot never sends it.

KEYBOARD_INSERT_TEXT = 1
KEYBOARD_DELETE_CHARACTERS = 4

DEVICE_INFO_SCREEN_SIZE = 1
DEVICE_INFO_SCREEN_SCALE = 3

TOUCH_UP = 0
TOUCH_DOWN = 1
TOUCH_MOVE = 2

DEFAULT_PORT = 6000

Field = Union[str, int, float]


class ProtocolError(ValueError):
    pass


def encode(task: int, *fields: Field) -> bytes:
    parts = [str(f) for f in fields]
    for part in parts:
        # A field containing the separators could smuggle extra fields or a second command.
        if ";;" in part or "\r" in part or "\n" in part:
            raise ProtocolError("field contains a ZXTouch separator (';;' or a newline)")
    return (str(task) + ";;".join(parts) + "\r\n").encode("utf-8")


def decode(data: bytes) -> tuple[bool, list[str]]:
    text = data.decode("utf-8", errors="replace").replace("\r\n", "")
    if not text:
        return False, ["empty reply"]
    fields = text.split(";;")
    if text[0] != "0":
        return False, fields[1:2] or ["ZXTouch reported an error without a message"]
    return True, fields[1:]


def touch_event(kind: int, finger: int, x: float, y: float) -> str:
    # Pixel coordinates (verified on iOS 16), sent x10 and zero-padded to 5 digits as upstream.
    if not 0 <= finger <= 19:
        raise ProtocolError("finger index must be 0–19")
    xi, yi = round(x * 10), round(y * 10)
    if not (0 <= xi <= 99999 and 0 <= yi <= 99999):
        raise ProtocolError(f"touch coordinates out of range: ({x}, {y})")
    return f"{kind}{finger:02d}{xi:05d}{yi:05d}"


def touch(events: Sequence[str]) -> bytes:
    if not 1 <= len(events) <= 9:
        raise ProtocolError("1–9 touch events per packet")
    return encode(TASK_PERFORM_TOUCH, str(len(events)) + "".join(events))


def toast(content: str, duration: int, toast_type: int = 0, position: int = 0) -> bytes:
    """Fields: type, content, duration (s), position (0 top / 1 bottom), font size. VERIFY style."""
    return encode(TASK_SHOW_TOAST, toast_type, content, duration, position, 0)


def ocr_request(x: float, y: float, width: float, height: float) -> bytes:
    # Fields: mode=1, rect, custom words, min height, level (0=accurate), languages, autocorrect,
    # debug image path. Verified on iOS 16.
    rect = ",,".join(str(int(v)) for v in (x, y, width, height))
    return encode(TASK_TEXT_RECOGNIZER, 1, rect, "", "", 0, "", 0, "")


def parse_ocr(fields: Sequence[str]) -> list[tuple[str, float, float, float, float]]:
    """Each field is ``text,,x,,y,,width,,height`` in pixels, top-left origin (verified, iOS 16)."""
    out: list[tuple[str, float, float, float, float]] = []
    for raw in fields:
        parts = raw.split(",,")
        if len(parts) < 5 or not parts[0].strip():
            continue
        try:
            x, y, w, h = (float(p) for p in parts[1:5])
        except ValueError:
            continue
        out.append((parts[0], x, y, w, h))
    return out
