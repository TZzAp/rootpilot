"""``rootpilot daemon-plist``: a launchd plist that keeps the control page running.

Generated rather than shipped as a fixed file because the paths differ between rootful
(``/usr/bin/python3``, ``/Library/LaunchDaemons``) and rootless jailbreaks (everything under
``/var/jb``). It uses the Python and the RootPilot checkout that are running it.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from agent.paths import jbroot, state_dir

LABEL = "com.rootpilot.serve"


def daemon_plist(home: str = "/var/mobile") -> bytes:
    root = jbroot()
    checkout = Path(__file__).resolve().parent.parent
    bins = [str(root / p) for p in ("usr/bin", "bin", "usr/sbin", "sbin")]
    path = ":".join(dict.fromkeys([*bins, "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    log = str(state_dir({"HOME": home}) / "serve.log")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "agent", "serve"],
        "WorkingDirectory": str(checkout),
        "EnvironmentVariables": {"PYTHONPATH": str(checkout), "HOME": home, "PATH": path},
        "UserName": "mobile",
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    return plistlib.dumps(plist)


def install_path() -> Path:
    return jbroot() / "Library" / "LaunchDaemons" / f"{LABEL}.plist"
