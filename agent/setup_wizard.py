"""``rootpilot setup``: choose a model provider and store its API key in a 0600 config file."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

from agent.config import default_config_path
from agent.errors import ConfigError
from agent.paths import ensure_private_dir


@dataclass(frozen=True)
class Choice:
    provider: str
    label: str
    key_field: str
    default_model: str  # empty: the user must name one
    key_prefix: str = ""  # for a gentle sanity check only


CHOICES = (
    Choice("anthropic", "Anthropic (Claude)", "anthropic_api_key", "claude-opus-5-5", "sk-ant-"),
    Choice("deepseek", "DeepSeek", "deepseek_api_key", "deepseek-flash", "sk-"),
    Choice("openai", "OpenAI", "openai_api_key", "", "sk-"),
    Choice("compat", "OpenAI-compatible server (local model)", "compat_api_key", ""),
)


def _read_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def write_config(path: Path, data: dict[str, Any]) -> None:
    """Write atomically with mode 0600 from the first byte (never briefly world-readable)."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def setup(
    args: argparse.Namespace,
    out: TextIO,
    read: Callable[[str], str] = input,
    read_secret: Callable[[str], str] = getpass.getpass,
    stdin: Optional[TextIO] = None,
) -> int:
    path = Path(args.config) if args.config else default_config_path()
    data = _read_existing(path)
    by_name = {c.provider: c for c in CHOICES}

    choice = by_name.get(args.provider or "")
    if choice is None:
        current = data.get("provider", "anthropic")
        out.write("Which model provider should RootPilot use?\n")
        for i, c in enumerate(CHOICES, 1):
            mark = "  (current)" if c.provider == current else ""
            out.write(f"  {i}. {c.label}{mark}\n")
        default = next((i for i, c in enumerate(CHOICES, 1) if c.provider == current), 1)
        while choice is None:
            answer = read(f"Choose 1-{len(CHOICES)} [{default}]: ").strip() or str(default)
            if answer.isdigit() and 1 <= int(answer) <= len(CHOICES):
                choice = CHOICES[int(answer) - 1]

    if choice.provider == "compat":
        url = read(f"Server base URL [{data.get('compat_base_url', 'http://127.0.0.1:8080/v1')}]: ")
        data["compat_base_url"] = url.strip() or data.get(
            "compat_base_url", "http://127.0.0.1:8080/v1"
        )

    if args.key_stdin:
        key = (stdin or sys.stdin).readline().strip()
    else:
        keep = " (Enter keeps the current one)" if data.get(choice.key_field) else ""
        optional = " (optional)" if choice.provider == "compat" else ""
        key = read_secret(f"{choice.label} API key{optional}, input hidden{keep}: ").strip()
    if key:
        if choice.key_prefix and not key.startswith(choice.key_prefix):
            out.write(f"warning: {choice.label} keys usually start with {choice.key_prefix!r}\n")
        data[choice.key_field] = key
    elif not data.get(choice.key_field) and choice.provider != "compat":
        out.write(f"error: no API key given for {choice.label}\n")
        return 1

    model = args.model
    if not model:
        suggested = data.get("model") if data.get("provider") == choice.provider else None
        suggested = suggested or choice.default_model
        if args.key_stdin:
            model = suggested
        else:
            prompt = f"Model [{suggested}]: " if suggested else "Model (required): "
            model = read(prompt).strip() or suggested
    if not model:
        out.write(f"error: {choice.label} needs a model name\n")
        return 1

    data["provider"] = choice.provider
    data["model"] = model
    data.setdefault("backend", args.backend or "zxtouch")
    if args.backend:
        data["backend"] = args.backend
    write_config(path, data)
    out.write(
        f"Saved {path} (mode 0600): provider {choice.provider}, model {model}, "
        f"key {'stored' if data.get(choice.key_field) else 'none'}.\n"
        "Next: rootpilot doctor\n"
    )
    return 0
