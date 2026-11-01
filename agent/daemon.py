"""``rootpilot daemon-plist`` and the launchd start-up of ``rootpilot serve``.

The plist is generated rather than shipped as a fixed file because the paths differ between
rootful (``/usr/bin/python3``, ``/Library/LaunchDaemons``) and rootless jailbreaks (everything
under ``/var/jb``). It uses the Python and the RootPilot checkout that are running it.

Memory: iOS gives launchd daemons without a budget an 18 MB hard memory limit. Python plus a
running agent needs more, so the kernel killed the control server the moment a run started
(``EXC_RESOURCE ... exceeded mem limit: InactiveHard 18 MB (fatal)``). ``JetsamProperties`` in
the plist is ignored for third-party daemons, and only root may change a limit. So the job
starts as root, raises its own limit with ``memorystatus_control`` (still a hard cap, so a
runaway can't eat the phone), then permanently drops to ``mobile`` before loading anything
else — config, keys and the agent never run as root.
"""

from __future__ import annotations

import ctypes
import os
import plistlib
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from agent.errors import RootPilotError
from agent.paths import jbroot, state_dir

LABEL = "com.rootpilot.serve"
MEMORY_LIMIT_MB = 300
_SET_MEMLIMIT_PROPERTIES = 7  # MEMORYSTATUS_CMD_SET_MEMLIMIT_PROPERTIES
_ATTR_FATAL = 0x1


class _MemlimitProperties(ctypes.Structure):
    _fields_ = [
        ("memlimit_active", ctypes.c_int32),
        ("memlimit_active_attr", ctypes.c_uint32),
        ("memlimit_inactive", ctypes.c_int32),
        ("memlimit_inactive_attr", ctypes.c_uint32),
    ]


def raise_memory_limit(mb: int, libc: Optional[Any] = None) -> bool:
    """Set this process's active and inactive jetsam limits (root only). True on success."""
    try:
        libc = libc or ctypes.CDLL(None, use_errno=True)
        props = _MemlimitProperties(mb, _ATTR_FATAL, mb, _ATTR_FATAL)
        rc = libc.memorystatus_control(
            _SET_MEMLIMIT_PROPERTIES, os.getpid(), 0, ctypes.byref(props), ctypes.sizeof(props)
        )
    except (OSError, AttributeError):
        return False
    return int(rc) == 0


def prepare_service(
    env: Optional[dict[str, str]] = None,
    getuid: Callable[[], int] = os.getuid,
    raise_limit: Callable[[int], bool] = raise_memory_limit,
    drop: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Called first by ``rootpilot serve``. Returns a note for the log, if any."""
    env = dict(os.environ) if env is None else env
    if getuid() != 0:
        return None
    user = env.get("ROOTPILOT_RUN_AS", "")
    if not user or user == "root":
        raise RootPilotError(
            "refusing to run the control server as root; launch it with ROOTPILOT_RUN_AS=mobile "
            "(rootpilot daemon-plist does this)"
        )
    mb = int(env.get("ROOTPILOT_MEMORY_LIMIT_MB", MEMORY_LIMIT_MB))
    raised = raise_limit(mb)
    (drop or _drop_privileges)(user)
    state = "raised to" if raised else "NOT raised (the default may be 18 MB)"
    return f"memory limit {state} {mb} MB; running as {user}"


def _drop_privileges(user: str) -> None:
    import pwd

    pw = pwd.getpwnam(user)
    os.initgroups(user, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    if os.getuid() == 0 or os.geteuid() == 0:
        raise RootPilotError("failed to drop root privileges")
    os.environ.update(USER=user, LOGNAME=user)


def stable_path(path: str, root: Path, realpath: Callable[[str], str] = os.path.realpath) -> str:
    """Rewrite a path under the resolved jailbreak root to go through ``root`` instead.

    Dopamine's ``/var/jb`` points into a randomly named preboot folder that can change when the
    jailbreak is set up again; a plist with the resolved path would then stop working.
    """
    if str(root) == "/":
        return path
    real = realpath(str(root)).rstrip("/")
    if path == real or path.startswith(real + "/"):
        return str(root) + path[len(real) :]
    return path


def daemon_plist(home: Optional[str] = None) -> bytes:
    # The daemon must see the same home as the shell that ran install-icon and setup (on
    # rootless that is /var/jb/var/mobile), or it would look for a different token and config.
    home = home or str(Path.home())
    root = jbroot()
    checkout = stable_path(str(Path(__file__).resolve().parent.parent), root)
    bins = [str(root / p) for p in ("usr/bin", "bin", "usr/sbin", "sbin")]
    path = ":".join(dict.fromkeys([*bins, "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    log = str(state_dir({"HOME": home}) / "serve.log")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [stable_path(sys.executable, root), "-m", "agent", "serve"],
        "WorkingDirectory": checkout,
        "EnvironmentVariables": {
            "PYTHONPATH": checkout,
            "HOME": home,
            "PATH": path,
            # starts as root only to raise its memory limit, then drops to this user
            "ROOTPILOT_RUN_AS": "mobile",
            "ROOTPILOT_MEMORY_LIMIT_MB": str(MEMORY_LIMIT_MB),
            # stdout is a file under launchd; without this, serve.log stays empty for hours
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    return plistlib.dumps(plist)


def install_path() -> Path:
    return jbroot() / "Library" / "LaunchDaemons" / f"{LABEL}.plist"
