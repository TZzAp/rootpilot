"""Filesystem locations. Never hardcode the jailbreak root; always go through ``jbroot()``."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Optional


def jbroot(
    env: Optional[Mapping[str, str]] = None,
    isdir: Callable[[str], bool] = os.path.isdir,
) -> Path:
    """Resolve the jailbreak root (SPEC §2.1): override, RootHide, rootless, then rootful ``/``."""
    env = os.environ if env is None else env
    for var in ("ROOTPILOT_JBROOT", "JBROOT"):
        value = env.get(var)
        if value:
            return Path(value)
    if isdir("/var/jb"):
        return Path("/var/jb")
    return Path("/")


def _xdg(env: Mapping[str, str], var: str, fallback: str) -> Path:
    value = env.get(var)
    if value:
        return Path(value)
    return Path(env.get("HOME", str(Path.home()))) / fallback


def config_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return _xdg(env, "XDG_CONFIG_HOME", ".config") / "rootpilot"


def state_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return _xdg(env, "XDG_STATE_HOME", ".local/state") / "rootpilot"


def stop_file(env: Optional[Mapping[str, str]] = None) -> Path:
    """Creating this file stops a running agent before its next action."""
    return state_dir(env) / "STOP"


def hud_file(env: Optional[Mapping[str, str]] = None) -> Path:
    """State the SpringBoard HUD overlay reads: whether the agent is active and its step."""
    return state_dir(env) / "hud.json"


def hud_status_file(env: Optional[Mapping[str, str]] = None) -> Path:
    """Written by the overlay itself (hud/RootPilotHUD.m) when it loads, shows and hides."""
    return state_dir(env) / "hud.status.json"


def serve_token_file(env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / "serve_token"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path
