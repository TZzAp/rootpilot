from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Callable

import pytest

from agent.backends.hud_ocr import HudOCR
from agent.backends.zxtouch import ZXTouchBackend
from agent.errors import BackendError, BackendTimeout
from agent.types import Size


def overlay(
    folder: Path, items: list[dict[str, Any]], error: str = "", stale: bool = False
) -> Callable[[str], None]:
    """Stands in for SpringBoard: answers the request it finds in the folder."""

    def notify(name: str) -> None:
        assert name == "com.rootpilot.hud.ocr"
        req = json.loads((folder / "ocr.request").read_text())
        rid = "someone-else" if stale else req["id"]
        (folder / "ocr.result.json").write_text(
            json.dumps({"id": rid, "items": items, "error": error, "w": 375, "h": 812})
        )

    return notify


def test_hud_ocr_round_trip(tmp_path: Path) -> None:
    items = [{"text": "General", "x": 20, "y": 380, "w": 80, "h": 20, "c": 1.0}]
    ocr = HudOCR(folder=tmp_path, notify=overlay(tmp_path, items))
    assert ocr.read() == [("General", 20.0, 380.0, 80.0, 20.0)]
    assert (tmp_path.stat().st_mode & 0o777) == 0o700


def test_hud_ocr_errors_and_stale_results(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="busy"):
        HudOCR(folder=tmp_path, notify=overlay(tmp_path, [], error="busy")).read()
    # a result for another request id is never accepted
    with pytest.raises(BackendTimeout):
        HudOCR(folder=tmp_path, timeout=0.3, notify=overlay(tmp_path, [], stale=True)).read()


def test_zxtouch_uses_overlay_ocr_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.backends.zxtouch as zx

    monkeypatch.setattr(zx, "hud_installed", lambda: True)
    dev = ZXTouchBackend()
    monkeypatch.setattr(dev, "screen_size", lambda: Size(375, 812))
    monkeypatch.setattr(HudOCR, "read", lambda self: [("About", 20.0, 150.0, 60.0, 20.0)])
    assert dev.ocr_engine() == "overlay"
    assert [n.label for n in dev.ui_tree().children] == ["About"]  # type: ignore[union-attr]

    def timeout(self: HudOCR) -> list[Any]:
        raise BackendTimeout("not loaded")

    monkeypatch.setattr(HudOCR, "read", timeout)
    monkeypatch.setattr(dev, "_pixel_size", lambda: Size(1125, 2436))
    monkeypatch.setattr(dev, "scale", lambda: 3.0)
    monkeypatch.setattr(dev, "_request", lambda packet: ["Settings,,60,,300,,180,,60"])
    assert [n.label for n in dev.ui_tree().children] == ["Settings"]  # type: ignore[union-attr]
    assert dev.ocr_engine() == "zxtouch"  # stays on the fallback for this session
    with pytest.raises(Exception, match="ocr must be"):
        ZXTouchBackend(ocr="magic")


class ResettingSocket:
    """What SpringBoard dying mid-request looks like from the client side."""

    def sendall(self, data: bytes) -> None: ...

    def recv(self, n: int) -> bytes:
        raise ConnectionResetError(54, "Connection reset by peer")

    def close(self) -> None: ...


def test_connection_reset_becomes_backend_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kw: ResettingSocket())
    dev = ZXTouchBackend()
    with pytest.raises(BackendError, match="connection lost"):
        dev._request(b"251\r\n")


def test_control_page_never_stuck_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from agent.config import Config
    from agent.serve import ControlServer

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    def broken_builder(state: Any, task: str) -> Any:
        raise RuntimeError("no API key")

    server = ControlServer(Config(serve_port=0), build_agent=broken_builder, token="t")
    assert server.start_run("x")
    for _ in range(100):
        if not server.state.running:
            break
        time.sleep(0.02)
    snap = server.state.snapshot()
    assert not snap["running"] and snap["result"]["reason"] == "error"
    assert "no API key" in snap["result"]["answer"]


# ---- clipboard, kill switch, benchmark mode ------------------------------------------


def test_hud_clipboard_round_trip(tmp_path: Path) -> None:
    from agent.backends.hud_ocr import HudClipboard

    store: dict[str, str] = {}

    def overlay(name: str) -> None:
        assert name == "com.rootpilot.hud.clipboard"
        req = json.loads((tmp_path / "clipboard.request").read_text())
        if req["op"] == "set":
            store["text"] = req["text"]
            out = {"id": req["id"], "ok": True}
        else:
            out = {"id": req["id"], "ok": True, "text": store.get("text", "")}
        (tmp_path / "clipboard.result.json").write_text(json.dumps(out))

    clip = HudClipboard(folder=tmp_path, notify=overlay)
    clip.set("lantern")
    assert clip.get() == "lantern"
    assert not (tmp_path / "clipboard.result.json").exists()  # user text isn't left behind


def test_kill_switch_stops_the_run(tmp_path: Path) -> None:
    from agent.backends.mock import DEMO_SCREENS, MockBackend
    from agent.types import Completion, ToolCall, Usage
    from tests.test_loop import make_agent

    kill = tmp_path / "KILL"
    kill.write_text("{}")  # left over from an earlier run: must not stop this one

    def press_lock(messages: Any) -> Completion:
        kill.write_text(json.dumps({"source": "lock button"}))
        return Completion(
            "", [ToolCall("a", "launch_app", {"bundle_id": "com.apple.Preferences"})], Usage()
        )

    device = MockBackend.from_dir(DEMO_SCREENS)
    agent, _, _ = make_agent(device, [press_lock], tmp_path)
    agent.kill_file = kill
    result = agent.run("anything")
    assert (
        result.reason == "stopped" and result.answer == "Stopped by the kill switch (lock button)."
    )
    assert not any(op == "launch_app" for op, _ in device.calls)  # nothing after the kill


def test_benchmark_agents_never_wait_for_a_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.cli import make_agent_for_serve
    from agent.loop import NO
    from agent.serve import ControlState

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("ROOTPILOT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-" + "b" * 32)
    monkeypatch.setenv("ROOTPILOT_BACKEND", "mock")
    state = ControlState()
    agent = make_agent_for_serve(state, {"max_steps": 5}, bench=True)
    assert agent.registry.get("shell") is None and agent.registry.get("ask_user") is None
    assert agent.registry.get("clipboard_get") is None and agent.registry.get("tap") is not None
    assert agent.confirm(None, {}, None) == NO  # type: ignore[arg-type]
    assert "continue on your own" in agent.ask_user("?")
    assert state.pending is None and agent.config.max_steps == 5
    normal = make_agent_for_serve(state)
    assert normal.registry.get("shell") is not None and normal.kill_file is not None
