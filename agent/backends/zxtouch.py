"""Backend over the ZXTouch tweak's socket (M0/M1 path until the M2 helper exists).

ZXTouch gives us touch, text input, app launch, screen size and OCR, but no screenshots, UI tree,
hardware buttons or foreground-app query. The UI tree here is synthesised from OCR, and "home"
is the bottom-edge swipe gesture. Only loopback hosts are accepted: ZXTouch has no
authentication and can run root shell commands, so it must never be reachable from the network.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
import time
from typing import Optional

from agent.backends import zxtouch_protocol as proto
from agent.errors import BackendError, BackendTimeout, BackendUnsupported, ConfigError
from agent.paths import jbroot
from agent.types import Button, Capability, Image, Rect, Size, UINode

_FINGER = 5
_MAX_REPLY = 1 << 20


def require_loopback(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ConfigError(f"cannot resolve ZXTouch host {host!r}: {exc}") from exc
    addresses = {info[4][0] for info in infos}
    bad = [a for a in addresses if not ipaddress.ip_address(str(a).split("%")[0]).is_loopback]
    if not addresses or bad:
        raise ConfigError(
            f"refusing ZXTouch host {host!r}: only loopback is allowed (resolved to "
            f"{', '.join(sorted(map(str, addresses)))}). Run RootPilot on the device itself."
        )


class ZXTouchBackend:
    name = "zxtouch"

    def __init__(
        self, host: str = "127.0.0.1", port: int = proto.DEFAULT_PORT, timeout: float = 5.0
    ) -> None:
        require_loopback(host, port)
        self._addr = (host, port)
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._size: Optional[Size] = None
        self._scale: Optional[float] = None

    # ---- transport ----

    def _conn(self) -> socket.socket:
        if self._sock is None:
            try:
                self._sock = socket.create_connection(self._addr, timeout=self._timeout)
            except OSError as exc:
                raise BackendError(
                    f"cannot connect to ZXTouch at {self._addr[0]}:{self._addr[1]}: {exc}"
                ) from exc
        return self._sock

    def _send(self, packet: bytes) -> None:
        try:
            self._conn().sendall(packet)
        except OSError as exc:
            self.close()
            raise BackendError(f"ZXTouch send failed: {exc}") from exc

    def _request(self, packet: bytes) -> list[str]:
        self._send(packet)
        sock = self._conn()
        try:
            while b"\r\n" not in self._buf:
                chunk = sock.recv(65536)
                if not chunk:
                    raise BackendError("ZXTouch closed the connection")
                self._buf += chunk
                if len(self._buf) > _MAX_REPLY:
                    raise BackendError("ZXTouch reply too large")
        except socket.timeout as exc:
            self.close()
            raise BackendTimeout("ZXTouch did not reply in time") from exc
        line, _, self._buf = self._buf.partition(b"\r\n")
        ok, fields = proto.decode(line)
        if not ok:
            raise BackendError(f"ZXTouch error: {fields[0] if fields else 'unknown'}")
        return fields

    # ---- Backend protocol ----

    def capabilities(self) -> frozenset[Capability]:
        caps = {Capability.TOUCH, Capability.TEXT_INPUT, Capability.LAUNCH_APP, Capability.UI_TREE}
        if self._uiopen():
            caps.add(Capability.OPEN_URL)
        return frozenset(caps)

    # ZXTouch works in pixels (verified on iOS 16: 1125x2436 on an iPhone X, scale 3). The rest
    # of RootPilot works in points, so convert at this boundary.

    def scale(self) -> float:
        if self._scale is None:
            fields = self._request(
                proto.encode(proto.TASK_GET_DEVICE_INFO, proto.DEVICE_INFO_SCREEN_SCALE)
            )
            try:
                self._scale = float(fields[0])
            except (IndexError, ValueError) as exc:
                raise BackendError(f"unexpected screen scale reply: {fields!r}") from exc
            if not 1 <= self._scale <= 4:
                raise BackendError(f"implausible screen scale {self._scale}")
        return self._scale

    def _pixel_size(self) -> Size:
        if self._size is None:
            fields = self._request(
                proto.encode(proto.TASK_GET_DEVICE_INFO, proto.DEVICE_INFO_SCREEN_SIZE)
            )
            try:
                self._size = Size(float(fields[0]), float(fields[1]))
            except (IndexError, ValueError) as exc:
                raise BackendError(f"unexpected screen size reply: {fields!r}") from exc
        return self._size

    def screen_size(self) -> Size:
        px, s = self._pixel_size(), self.scale()
        return Size(px.width / s, px.height / s)

    def screenshot(self) -> Image:
        raise BackendUnsupported("ZXTouch cannot take screenshots; use observe's UI tree (OCR)")

    def ui_tree(self) -> Optional[UINode]:
        px, s = self._pixel_size(), self.scale()
        fields = self._request(proto.ocr_request(0, 0, px.width, px.height))
        children = [
            UINode(role="Text", label=text, frame=Rect(x / s, y / s, w / s, h / s))
            for text, x, y, w, h in proto.parse_ocr(fields)
        ]
        children.sort(key=lambda n: (n.frame.y, n.frame.x) if n.frame else (0.0, 0.0))
        # Unlabelled root: OCR gives text only, no element roles or containers.
        return UINode(
            role="Screen", frame=Rect(0, 0, px.width / s, px.height / s), children=children
        )

    def _touch(self, kind: int, x: float, y: float) -> None:
        s = self.scale()
        self._send(proto.touch([proto.touch_event(kind, _FINGER, x * s, y * s)]))

    def tap(self, x: float, y: float) -> None:
        self._touch(proto.TOUCH_DOWN, x, y)
        time.sleep(0.05)
        self._touch(proto.TOUCH_UP, x, y)

    def long_press(self, x: float, y: float, duration_ms: int) -> None:
        self._touch(proto.TOUCH_DOWN, x, y)
        time.sleep(duration_ms / 1000)
        self._touch(proto.TOUCH_UP, x, y)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        # Hold still before lifting so lists stop where the finger stops instead of gliding on;
        # otherwise the next observation is taken mid-scroll and taps land on the wrong row.
        self._drag(x1, y1, x2, y2, duration_ms, hold_ms=250)

    def _drag(
        self, x1: float, y1: float, x2: float, y2: float, duration_ms: int, hold_ms: int
    ) -> None:
        steps = max(2, duration_ms // 16)
        self._touch(proto.TOUCH_DOWN, x1, y1)
        for i in range(1, steps + 1):
            t = i / steps
            time.sleep(duration_ms / 1000 / steps)
            self._touch(proto.TOUCH_MOVE, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        if hold_ms:
            time.sleep(hold_ms / 1000)
            self._touch(proto.TOUCH_MOVE, x2, y2)
        self._touch(proto.TOUCH_UP, x2, y2)

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch == "\b":
                self._request(
                    proto.encode(proto.TASK_KEYBOARD, proto.KEYBOARD_DELETE_CHARACTERS, 1)
                )
            else:
                self._request(proto.encode(proto.TASK_KEYBOARD, proto.KEYBOARD_INSERT_TEXT, ch))

    def press_button(self, button: Button) -> None:
        if button is not Button.HOME:
            raise BackendUnsupported(f"ZXTouch cannot press the {button.value} button")
        # Face ID iPhones: swipe up from the home indicator.
        size = self.screen_size()
        cx = size.width / 2
        self._drag(cx, size.height - 2, cx, size.height * 0.55, 250, hold_ms=0)  # a flick

    def launch_app(self, bundle_id: str) -> None:
        self._request(proto.encode(proto.TASK_PROCESS_BRING_FOREGROUND, bundle_id))

    def _uiopen(self) -> Optional[str]:
        found = shutil.which("uiopen")
        if found:
            return found
        candidate = jbroot() / "usr" / "bin" / "uiopen"
        return str(candidate) if candidate.exists() else None

    def open_url(self, url: str) -> None:
        tool = self._uiopen()
        if tool is None:
            raise BackendUnsupported("opening URLs needs uiopen (Procursus package uikittools)")
        result = subprocess.run(
            [tool, url], capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode != 0:
            raise BackendError(f"uiopen failed: {result.stderr.strip() or result.returncode}")

    def clipboard_get(self) -> str:
        raise BackendUnsupported("ZXTouch has no working clipboard API")

    def clipboard_set(self, text: str) -> None:
        raise BackendUnsupported("ZXTouch has no working clipboard API")

    def foreground_app(self) -> Optional[str]:
        return None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = b""


def listening_addresses(port: int = proto.DEFAULT_PORT) -> Optional[list[str]]:
    """Local addresses with a TCP listener on ``port`` (via netstat), or None if unknown."""
    netstat = shutil.which("netstat")
    if netstat is None:
        return None
    try:
        out = subprocess.run(
            [netstat, "-an", "-p", "tcp"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found: list[str] = []
    for line in out.splitlines():
        cols = line.split()
        if "LISTEN" not in cols or len(cols) < 4:
            continue
        local = cols[3]
        host, _, p = local.rpartition(".")
        if not host:
            host, _, p = local.rpartition(":")
        if p == str(port):
            found.append(host)
    return found


def is_exposed(addresses: list[str]) -> bool:
    """True if any listener is reachable from outside the device."""
    loopback = {"127.0.0.1", "::1", "localhost"}
    return any(a not in loopback for a in addresses)
