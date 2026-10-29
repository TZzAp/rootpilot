"""On-device "agent is in control" indicator (SPEC §3.5).

Makes it visible, on the phone itself, that RootPilot is driving the screen — the opposite of
silent automation. Two channels, both best-effort (never breaking a run):

- a ZXTouch toast banner (works today), and
- a state file plus Darwin notifications that the SpringBoard HUD overlay (``hud/``) listens to
  for the pulsing edge glow. Writing them is cheap and harmless when no overlay is installed.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from agent.paths import ensure_private_dir, jbroot

NOTIFY_PREFIX = "com.rootpilot.hud"
HUD_DYLIB = "Library/MobileSubstrate/DynamicLibraries/RootPilotHUD.dylib"
Point = Optional[tuple[float, float]]


class Indicator(Protocol):
    def start(self, task: str) -> None: ...
    def update(self, text: str, point: Point = None) -> None: ...
    def stop(self) -> None: ...


class NullIndicator:
    """Does nothing (tests, and runs where no on-device signal is wanted)."""

    def start(self, task: str) -> None: ...
    def update(self, text: str, point: Point = None) -> None: ...
    def stop(self) -> None: ...


def _notify_poster() -> Callable[[str], None]:
    """``notify_post`` from libSystem on iOS; a no-op elsewhere (e.g. Linux CI)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        post = libc.notify_post
        post.argtypes = [ctypes.c_char_p]
    except (OSError, AttributeError):
        return lambda name: None

    def notify(name: str) -> None:
        try:
            post(name.encode())
        except OSError:
            return

    return notify


class DeviceIndicator:
    def __init__(
        self,
        hud_file: Path,
        toast: Optional[Callable[[str, float], None]] = None,
        redact: Callable[[str], str] = lambda s: s,
    ) -> None:
        self._hud = hud_file
        self._toast = toast
        self._redact = redact
        self._notify = _notify_poster()
        self._active = False
        self._task = ""

    def _write(self, step: str, point: Point = None) -> None:
        payload = {
            "active": self._active,
            "task": self._task,
            "step": step,
            "point": [round(point[0], 1), round(point[1], 1)] if point else None,
        }
        try:
            ensure_private_dir(self._hud.parent)
            tmp = self._hud.with_name(self._hud.name + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._hud)
        except OSError:
            return

    def _banner(self, text: str, duration: float) -> None:
        if self._toast is None:
            return
        try:
            self._toast(text, duration)
        except Exception as exc:  # the indicator must never break a run
            _ = exc

    def start(self, task: str) -> None:
        self._active = True
        self._task = self._redact(task)[:120]
        self._write("")
        self._notify(f"{NOTIFY_PREFIX}.active")
        self._banner("🤖 RootPilot is controlling this iPhone", 4)

    def update(self, text: str, point: Point = None) -> None:
        if not self._active:
            return
        self._write(self._redact(text)[:120], point)
        self._notify(f"{NOTIFY_PREFIX}.update")

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._write("")
        self._notify(f"{NOTIFY_PREFIX}.idle")
        self._banner("✓ RootPilot finished", 3)


def hud_installed() -> bool:
    return (jbroot() / HUD_DYLIB).exists()


def overlay_status(status_file: Path) -> dict[str, Any]:
    """Installed / loaded (by a live SpringBoard) / visible, from the overlay's self-report."""
    out: dict[str, Any] = {"installed": hud_installed(), "loaded": False, "visible": False}
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
        pid = int(status.get("pid", 0))
        if pid > 0:
            os.kill(pid, 0)  # raises if that SpringBoard is gone
            out["loaded"] = bool(status.get("loaded"))
            out["visible"] = bool(status.get("visible"))
    except (OSError, ValueError, TypeError):
        pass
    return out


def make_indicator(backend: Any, hud_file: Path, redact: Callable[[str], str]) -> Indicator:
    # With the edge-glow overlay installed, skip the toast: it's text, and the agent reads the
    # screen with OCR, so a banner would leak into its first observation.
    toast = None if hud_installed() else getattr(backend, "toast", None)
    return DeviceIndicator(hud_file, toast=toast, redact=redact)
