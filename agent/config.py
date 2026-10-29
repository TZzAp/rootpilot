"""Configuration: JSON file < environment variables < CLI flags (SPEC §3.9)."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from agent.errors import ConfigError
from agent.paths import config_dir

SECRET_FIELDS = ("anthropic_api_key", "openai_api_key", "deepseek_api_key", "compat_api_key")

_ENV = {
    "ROOTPILOT_PROVIDER": "provider",
    "ROOTPILOT_MODEL": "model",
    "ROOTPILOT_BACKEND": "backend",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "ROOTPILOT_COMPAT_BASE_URL": "compat_base_url",
    "ROOTPILOT_COMPAT_API_KEY": "compat_api_key",
    "ROOTPILOT_ZXTOUCH_HOST": "zxtouch_host",
    "ROOTPILOT_ZXTOUCH_PORT": "zxtouch_port",
}


@dataclass
class Config:
    provider: str = "anthropic"
    model: str = ""
    backend: str = "zxtouch"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    compat_base_url: str = ""
    compat_api_key: str = ""
    compat_vision: bool = True  # set false for text-only local models

    zxtouch_host: str = "127.0.0.1"
    zxtouch_port: int = 6000

    max_steps: int = 40
    max_tokens: int = 400_000
    max_seconds: float = 600.0
    max_output_tokens: int = 16000  # thinking counts toward this
    image_max_edge: int = 1280
    keep_screenshots: int = 3
    save_screens: bool = False

    # Permission overrides (SPEC §3.4). Deny always wins over allow.
    sensitive_apps: list[str] = field(default_factory=list)
    irreversible_words: list[str] = field(default_factory=list)
    shell_allow_reboot: bool = False
    category_defaults: dict[str, str] = field(default_factory=dict)

    def secrets(self) -> list[str]:
        return [str(getattr(self, name)) for name in SECRET_FIELDS if getattr(self, name)]


def default_config_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return config_dir(env) / "config.json"


def _coerce(name: str, value: Any) -> Any:
    kinds = {f.name: f.type for f in fields(Config)}
    kind = str(kinds[name])
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
        if kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"config field {name!r} must be {kind}, got {value!r}") from exc
    return value


def load_config(
    path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Config:
    env = os.environ if env is None else env
    path = path or default_config_path(env)
    cfg = Config()
    known = {f.name for f in fields(Config)}

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path} must contain a JSON object")
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"unknown config keys in {path}: {', '.join(sorted(unknown))}")
        if any(data.get(k) for k in SECRET_FIELDS):
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise ConfigError(
                    f"{path} contains API keys but has mode {oct(mode)}; run: chmod 600 {path}"
                )
        for key, value in data.items():
            setattr(cfg, key, _coerce(key, value))

    for var, name in _ENV.items():
        if env.get(var):
            setattr(cfg, name, _coerce(name, env[var]))

    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key not in known:
            raise ConfigError(f"unknown option {key!r}")
        setattr(cfg, key, _coerce(key, value))
    return cfg
