from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agent.audit import AuditLog
from agent.config import load_config
from agent.errors import ConfigError
from agent.paths import jbroot, stop_file
from agent.redact import MASK, Redactor

ANTHROPIC_KEY = "sk-ant-api03-" + "A" * 40
OPENAI_KEY = "sk-proj-" + "b" * 40


# ---- paths ---------------------------------------------------------------------------


def test_jbroot_rootful_when_no_var_jb() -> None:
    assert jbroot(env={}, isdir=lambda p: False) == Path("/")


def test_jbroot_rootless() -> None:
    assert jbroot(env={}, isdir=lambda p: p == "/var/jb") == Path("/var/jb")


def test_jbroot_overrides_win() -> None:
    assert jbroot(env={"JBROOT": "/var/containers/x"}, isdir=lambda p: True) == Path(
        "/var/containers/x"
    )
    env = {"ROOTPILOT_JBROOT": "/opt/jb", "JBROOT": "/other"}
    assert jbroot(env=env, isdir=lambda p: True) == Path("/opt/jb")


def test_stop_file_uses_xdg_state_home(tmp_path: Path) -> None:
    assert stop_file({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "rootpilot" / "STOP"


# ---- config --------------------------------------------------------------------------


def test_config_precedence(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "from-file", "max_steps": 5}))
    cfg = load_config(path, env={"ROOTPILOT_MODEL": "from-env"}, overrides={"max_steps": "7"})
    assert cfg.model == "from-env"
    assert cfg.max_steps == 7


def test_config_refuses_world_readable_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"anthropic_api_key": ANTHROPIC_KEY}))
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(path, env={})
    os.chmod(path, 0o600)
    assert load_config(path, env={}).anthropic_api_key == ANTHROPIC_KEY


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"modle": "typo"}))
    with pytest.raises(ConfigError, match="unknown config keys"):
        load_config(path, env={})


# ---- redaction -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"key is {ANTHROPIC_KEY} ok",
        f"{OPENAI_KEY}",
        "Authorization: Bearer abcdefghijklmnop",
        "x-api-key: supersecretvalue123",
        "gho_" + "x" * 36,
    ],
)
def test_redactor_masks_known_secret_shapes(text: str) -> None:
    out = Redactor().text(text)
    assert MASK in out
    for secret in (ANTHROPIC_KEY, OPENAI_KEY, "abcdefghijklmnop", "supersecretvalue123", "x" * 36):
        assert secret not in out


def test_redactor_masks_literals_and_sensitive_keys() -> None:
    r = Redactor(literals=["hunter2-password"])
    data = {
        "note": "pw is hunter2-password",
        "api_key": "anything",
        "nested": [{"token": "t0k3n"}, {"label": "General"}],
    }
    out = r.obj(data)
    assert out["note"] == f"pw is {MASK}"
    assert out["api_key"] == MASK
    assert out["nested"][0]["token"] == MASK
    assert out["nested"][1]["label"] == "General"


def test_redactor_leaves_short_literals_alone() -> None:
    assert Redactor(literals=["ab"]).text("tab") == "tab"


# ---- audit log -----------------------------------------------------------------------


def test_audit_log_is_private_and_redacted(tmp_path: Path) -> None:
    log = AuditLog.for_run(tmp_path, "run1", Redactor(literals=[ANTHROPIC_KEY]))
    log.event("model_request", headers={"x-api-key": ANTHROPIC_KEY}, note=f"k={ANTHROPIC_KEY}")
    log.close()
    assert log.path is not None
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.path.parent.stat().st_mode) == 0o700
    raw = log.path.read_text()
    assert ANTHROPIC_KEY not in raw
    record = json.loads(raw.splitlines()[0])
    assert record["event"] == "model_request"
    assert record["headers"]["x-api-key"] == MASK
