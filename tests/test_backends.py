from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from agent.backends import zxtouch_protocol as proto
from agent.backends.mock import MockBackend
from agent.backends.zxtouch import ZXTouchBackend, is_exposed, require_loopback
from agent.errors import BackendUnsupported, ConfigError
from agent.types import Button

# ---- mock backend --------------------------------------------------------------------


def test_mock_follows_transitions(mock_device: MockBackend) -> None:
    mock_device.launch_app("com.apple.Preferences")
    assert mock_device.current.name == "settings"
    mock_device.tap(188, 406)  # "General" cell
    assert mock_device.current.name == "general"
    mock_device.tap(5, 5)  # nothing there
    assert mock_device.current.name == "general"
    mock_device.press_button(Button.HOME)
    assert mock_device.current.name == "start"
    assert mock_device.foreground_app() == "com.apple.springboard"
    assert ("tap", (188, 406)) in mock_device.calls


def test_mock_swipe_direction(mock_device: MockBackend) -> None:
    mock_device.launch_app("com.apple.Preferences")
    mock_device.swipe(187, 810, 187, 450, 250)
    assert mock_device.current.name == "start"


# ---- ZXTouch protocol ----------------------------------------------------------------


def test_protocol_encoding_matches_upstream_client() -> None:
    assert proto.encode(11, "com.apple.Preferences") == b"11com.apple.Preferences\r\n"
    assert proto.encode(24, 1, "a") == b"241;;a\r\n"
    assert proto.touch([proto.touch_event(1, 5, 12.3, 456.7)]) == b"1011050012304567\r\n"


def test_protocol_rejects_separator_injection() -> None:
    with pytest.raises(proto.ProtocolError):
        proto.encode(24, 1, "x;;13rm -rf /")
    with pytest.raises(proto.ProtocolError):
        proto.encode(11, "com.x\r\n13id")


def test_protocol_decode_and_ocr_parsing() -> None:
    assert proto.decode(b"0;;375;;812\r\n") == (True, ["375", "812"])
    assert proto.decode(b"1;;no such app\r\n") == (False, ["no such app"])
    boxes = proto.parse_ocr(["General,,20,,380,,80,,20", "", "bad,,x,,1,,2,,3"])
    assert boxes == [("General", 20.0, 380.0, 80.0, 20.0)]


# ---- ZXTouch backend -----------------------------------------------------------------


@pytest.mark.parametrize("host", ["192.0.2.10", "8.8.8.8", "10.0.0.1"])
def test_zxtouch_refuses_non_loopback(host: str) -> None:
    with pytest.raises(ConfigError, match="only loopback"):
        require_loopback(host, 6000)


def test_zxtouch_accepts_loopback() -> None:
    require_loopback("127.0.0.1", 6000)
    require_loopback("::1", 6000)


def test_exposure_check() -> None:
    assert is_exposed(["*"])
    assert is_exposed(["127.0.0.1", "192.0.2.10"])
    assert not is_exposed(["127.0.0.1", "::1"])


class FakeZXTouch:
    """Answers like the tweak over a socketpair, so the test opens no listening socket."""

    def __init__(self) -> None:
        self.client, self.server = socket.socketpair()
        self.received: list[bytes] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        buf = b""
        while True:
            data = self.server.recv(4096)
            if not data:
                return
            buf += data
            while b"\r\n" in buf:
                line, _, buf = buf.partition(b"\r\n")
                self.received.append(line)
                if line.startswith(b"10"):
                    continue  # touches get no reply
                if line == b"251":
                    self.server.sendall(b"0;;375;;812\r\n")
                elif line.startswith(b"27"):
                    self.server.sendall(
                        b"0;;About,,20,,150,,60,,20;;iOS Version,,20,,195,,90,,20\r\n"
                    )
                else:
                    self.server.sendall(b"0\r\n")


@pytest.fixture
def fake_zx(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeZXTouch]:
    fake = FakeZXTouch()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake.client)
    yield fake
    fake.client.close()
    fake.server.close()


def test_zxtouch_backend_round_trip(fake_zx: FakeZXTouch) -> None:
    dev = ZXTouchBackend()
    size = dev.screen_size()
    assert (size.width, size.height) == (375, 812)
    tree = dev.ui_tree()
    assert tree is not None
    assert [c.label for c in tree.children] == ["About", "iOS Version"]
    dev.tap(20, 30)
    dev.type_text("hi")
    dev.launch_app("com.apple.Preferences")
    sent = fake_zx.received
    assert b"1011050020000300" in sent and b"1010050020000300" in sent
    assert b"241;;h" in sent and b"241;;i" in sent
    assert b"11com.apple.Preferences" in sent
    assert not any(line.startswith(b"13") for line in sent)  # never the root-shell task
    with pytest.raises(BackendUnsupported):
        dev.screenshot()
    with pytest.raises(BackendUnsupported):
        dev.press_button(Button.VOLUME_UP)
    dev.close()
