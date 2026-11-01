"""Helpers behind the control page's settings, system and history views (agent/serve.py).

Everything here runs behind the control server's loopback + token guard. API keys are
write-only: the page can set or replace one but never read it back.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from agent.config import SECRET_FIELDS, Config, default_config_path, load_config
from agent.errors import ConfigError, RootPilotError
from agent.paths import state_dir
from agent.providers import DEFAULT_MODELS, EFFORTS, PROVIDERS, make_provider
from agent.redact import Redactor
from agent.setup_wizard import write_config
from agent.types import Message, TextPart, ToolSpec

KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "deepseek": "deepseek_api_key",
    "openai": "openai_api_key",
    "compat": "compat_api_key",
}
SELECTABLE = tuple(p for p in PROVIDERS if p != "chatgpt")  # chatgpt arrives in M4
RUN_ID = re.compile(r"^[0-9A-Za-z-]{6,64}$")


def _raw_config() -> dict[str, Any]:
    path = default_config_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def config_view() -> dict[str, Any]:
    """Settings the page may show: everything except secret values."""
    try:
        cfg = load_config()
        error = ""
    except ConfigError as exc:
        cfg, error = Config(), str(exc)
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "default_model": DEFAULT_MODELS.get(cfg.provider, ""),
        "effort": cfg.effort,
        "max_steps": cfg.max_steps,
        "max_minutes": round(cfg.max_seconds / 60, 1),
        "max_tokens_k": round(cfg.max_tokens / 1000),
        "screenshots": cfg.screenshots,
        "compat_base_url": cfg.compat_base_url,
        "ocr": cfg.ocr,
        "keys": {p: bool(getattr(cfg, f)) for p, f in KEY_FIELDS.items()},
        "providers": list(SELECTABLE),
        "default_models": DEFAULT_MODELS,
        "efforts": list(EFFORTS),
        "error": error,
    }


def apply_config(update: dict[str, Any]) -> dict[str, Any]:
    """Validate a settings change from the page and write it (mode 0600)."""
    data = _raw_config()
    provider = str(update.get("provider", data.get("provider", "anthropic")))
    if provider not in SELECTABLE:
        raise ConfigError(f"provider must be one of {', '.join(SELECTABLE)}")
    if provider != data.get("provider"):
        data.pop("model", None)  # a model name belongs to one provider
    data["provider"] = provider
    if "model" in update:
        model = str(update["model"]).strip()
        if len(model) > 100 or not re.fullmatch(r"[A-Za-z0-9._:/-]*", model):
            raise ConfigError("model name contains unexpected characters")
        if model:
            data["model"] = model
        else:
            data.pop("model", None)
    if "effort" in update:
        effort = str(update["effort"])
        if effort not in EFFORTS:
            raise ConfigError(f"effort must be one of {', '.join(e or 'default' for e in EFFORTS)}")
        if effort:
            data["effort"] = effort
        else:
            data.pop("effort", None)
    if "max_steps" in update:
        data["max_steps"] = max(1, min(1000, int(update["max_steps"])))
    if "max_minutes" in update:
        data["max_seconds"] = round(max(0.5, min(240.0, float(update["max_minutes"]))) * 60)
    if "screenshots" in update:
        data["screenshots"] = bool(update["screenshots"])
    if "max_tokens_k" in update:
        # Long tasks (a whole game) resend a growing history every step: 1-3 million in total.
        data["max_tokens"] = max(10, min(20000, int(update["max_tokens_k"]))) * 1000
    if "compat_base_url" in update:
        url = str(update["compat_base_url"]).strip()
        if url and not url.startswith(("http://", "https://")):
            raise ConfigError("the server URL must start with http:// or https://")
        data["compat_base_url"] = url
    key = str(update.get("api_key", "")).strip()
    if key:
        if len(key) > 300 or any(c.isspace() for c in key):
            raise ConfigError("that does not look like an API key")
        data[KEY_FIELDS[provider]] = key
    if update.get("clear_key"):
        data.pop(KEY_FIELDS[provider], None)
    write_config(default_config_path(), data)
    load_config()  # fail loudly now rather than at the next run
    return config_view()


def test_provider() -> dict[str, Any]:
    """One tiny model call with the current settings. Costs a few tokens."""
    try:
        cfg = load_config()
        provider = make_provider(cfg, Redactor(cfg.secrets()))
    except (RootPilotError, NotImplementedError) as exc:
        return {"ok": False, "error": str(exc)}
    tool = ToolSpec("done", "Finish.", {"type": "object", "properties": {}})
    started = time.monotonic()
    try:
        reply = provider.complete(
            "Reply with the single word OK.",
            [Message("user", [TextPart("ping")])],
            [tool],
            256,
        )
    except RootPilotError as exc:
        return {"ok": False, "error": Redactor(cfg.secrets()).text(str(exc))}
    return {
        "ok": True,
        "provider": cfg.provider,
        "model": provider.model,
        "seconds": round(time.monotonic() - started, 2),
        "reply": (reply.text or ", ".join(c.name for c in reply.tool_calls))[:80],
    }


def doctor_view() -> list[dict[str, str]]:
    from agent.cli import doctor_checks

    args = argparse.Namespace(
        config=None, fixture=None, backend=None, provider=None, model=None,
        max_steps=None, save_screens=None,
    )  # fmt: skip
    return [{"name": n, "status": s, "detail": d} for n, s, d in doctor_checks(args)]


def run_details(run_id: str) -> Optional[dict[str, Any]]:
    """Steps of one run from its (already redacted) audit log."""
    if not RUN_ID.match(run_id):
        return None
    path = state_dir() / "runs" / f"{run_id}.jsonl"
    if not path.exists():
        return None
    steps: list[dict[str, Any]] = []
    start: dict[str, Any] = {}
    end: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind, ts = e.get("event"), e.get("ts")
        if kind == "run_start":
            start = e
        elif kind == "run_end":
            end = e
        elif kind == "model_response":
            calls = ", ".join(
                f"{c['name']}({_short(c.get('args'))})" for c in e.get("tool_calls", [])
            )
            text = (e.get("text") or "").strip()
            steps.append({"ts": ts, "kind": "model", "step": e.get("step"), "text": text[:400],
                          "calls": calls, "tokens": e.get("output_tokens")})  # fmt: skip
        elif kind == "permission" and e.get("verdict") != "allow":
            steps.append(
                {"ts": ts, "kind": "permission", "text": f"{e.get('verdict')}: {e.get('reason')}"}
            )
        elif kind == "tool_result" and e.get("is_error"):
            steps.append(
                {"ts": ts, "kind": "error", "text": f"{e.get('name')}: {e.get('summary')}"}
            )
        elif kind == "error":
            steps.append({"ts": ts, "kind": "error", "text": str(e.get("message"))})
    return {
        "run_id": run_id,
        "task": start.get("task", ""),
        "model": start.get("model", ""),
        "provider": start.get("provider", ""),
        "answer": end.get("answer"),
        "success": end.get("success"),
        "reason": end.get("reason"),
        "input_tokens": end.get("input_tokens"),
        "output_tokens": end.get("output_tokens"),
        "seconds": round(end["ts"] - start["ts"], 1) if end and start else None,
        "steps": steps,
    }


def _short(args: Any) -> str:
    text = json.dumps(args, ensure_ascii=False) if args else ""
    return text[1:-1][:80] if text.startswith("{") else text[:80]


def clear_history(keep_run: str = "") -> int:
    """Delete run logs (and saved screenshots), except the run in progress."""
    runs = state_dir() / "runs"
    removed = 0
    for path in runs.glob("*"):
        if keep_run and path.name.startswith(keep_run):
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        removed += 1
    return removed


def screen_jpeg(width: int = 360) -> Optional[bytes]:
    """A fresh screenshot through the overlay, or None if it isn't installed."""
    from agent.capture import request_capture
    from agent.indicator import hud_installed

    if not hud_installed():
        return None
    folder = request_capture(frames=1, name="live", width=width)
    frame = Path(folder) / "0001.jpg"
    return frame.read_bytes() if frame.exists() else None


__all__ = [
    "SECRET_FIELDS",
    "apply_config",
    "clear_history",
    "config_view",
    "doctor_view",
    "run_details",
    "screen_jpeg",
    "test_provider",
]
