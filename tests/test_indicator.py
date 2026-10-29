from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent import indicator as ind
from agent.backends.mock import MockBackend
from agent.indicator import DeviceIndicator, make_indicator
from tests.test_loop import _call, make_agent, tap_label


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def start(self, task: str) -> None:
        self.events.append(("start", task))

    def update(self, text: str, point: Any = None) -> None:
        self.events.append(("update", point))

    def stop(self) -> None:
        self.events.append(("stop", None))


def test_device_indicator_writes_hud_state_and_toasts(tmp_path: Path) -> None:
    toasts: list[str] = []
    hud = tmp_path / "hud.json"
    i = DeviceIndicator(hud, toast=lambda text, d: toasts.append(text))
    i.start("open Settings")
    assert json.loads(hud.read_text()) == {
        "active": True,
        "task": "open Settings",
        "step": "",
        "point": None,
    }
    i.update('tap e4 "General"', (106.0, 134.25))
    state = json.loads(hud.read_text())
    assert state["step"] == 'tap e4 "General"' and state["point"] == [106.0, 134.2]
    i.stop()
    i.stop()  # idempotent
    assert json.loads(hud.read_text())["active"] is False
    assert len(toasts) == 2 and "controlling" in toasts[0]


def test_toast_failure_never_breaks_a_run(tmp_path: Path) -> None:
    def boom(text: str, duration: float) -> None:
        raise RuntimeError("socket gone")

    i = DeviceIndicator(tmp_path / "hud.json", toast=boom)
    i.start("x")
    i.stop()


def test_toast_is_skipped_when_the_overlay_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Backend:
        def toast(self, text: str, duration: float) -> None: ...

    monkeypatch.setattr(ind, "hud_installed", lambda: True)
    assert make_indicator(Backend(), tmp_path / "h.json", str)._toast is None  # type: ignore[attr-defined]
    monkeypatch.setattr(ind, "hud_installed", lambda: False)
    assert make_indicator(Backend(), tmp_path / "h.json", str)._toast is not None  # type: ignore[attr-defined]


def test_loop_drives_indicator_with_touch_points(mock_device: MockBackend, tmp_path: Path) -> None:
    steps = [
        _call("launch_app", {"bundle_id": "com.apple.Preferences"}),
        tap_label("General"),
        _call("swipe", {"x1": 187, "y1": 600, "x2": 187, "y2": 300}),
        _call("done", {"answer": "ok", "success": True}),
    ]
    agent, _, _ = make_agent(mock_device, steps, tmp_path)
    rec = Recorder()
    agent.indicator = rec
    agent.run("go")
    kinds = [k for k, _ in rec.events]
    assert kinds[0] == "start" and kinds[-1] == "stop"
    points = [p for k, p in rec.events if k == "update"]
    assert points[0] is None  # launch_app touches nothing
    assert points[1] == (187.5, 406.0)  # centre of the General cell
    assert points[2] == (187.0, 600.0)  # swipe start


def test_overlay_status_requires_a_live_springboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setattr(ind, "hud_installed", lambda: True)
    status = tmp_path / "hud.status.json"
    assert ind.overlay_status(status) == {"installed": True, "loaded": False, "visible": False}
    status.write_text(json.dumps({"loaded": True, "visible": True, "pid": os.getpid()}))
    assert ind.overlay_status(status) == {"installed": True, "loaded": True, "visible": True}
    status.write_text(json.dumps({"loaded": True, "visible": True, "pid": 2**22 + 12345}))
    assert ind.overlay_status(status)["loaded"] is False  # that SpringBoard is gone


def test_recent_runs_reads_audit_logs(tmp_path: Path) -> None:
    from agent.audit import AuditLog, recent_runs
    from agent.redact import Redactor

    for i, ok in enumerate((True, False)):
        log = AuditLog.for_run(tmp_path, f"run{i}", Redactor())
        log.event("run_start", run_id=f"run{i}", task=f"task {i}")
        log.event("run_end", answer=f"answer {i}", success=ok, reason="done", steps=i + 1)
        log.close()
    (tmp_path / "runs" / "broken.jsonl").write_text("{not json\n")
    runs = recent_runs(tmp_path / "runs")
    assert {r["task"] for r in runs} == {"task 0", "task 1"}
    assert {r["success"] for r in runs} == {True, False}


def test_webclip_install_and_remove(tmp_path: Path) -> None:
    import plistlib

    from agent.webclip import BUNDLE_NAME, CLIP_ID, install_webclip, remove_webclip

    assert BUNDLE_NAME == "F52B7D582C04519485969F6EBDB9D943.webclip"  # what the phone uses
    assert len(CLIP_ID) == 32
    (tmp_path / "com.rootpilot.control.webclip").mkdir()  # an older install
    bundle = install_webclip("http://127.0.0.1:8366/tok/", root=tmp_path)
    info = plistlib.loads((bundle / "Info.plist").read_bytes())
    assert info["URL"] == "http://127.0.0.1:8366/tok/" and info["FullScreen"] is True
    assert (bundle / "icon.png").read_bytes()[:4] == b"\x89PNG"
    assert not (tmp_path / "com.rootpilot.control.webclip").exists()
    assert remove_webclip(tmp_path) and not bundle.exists()
    assert not remove_webclip(tmp_path)
