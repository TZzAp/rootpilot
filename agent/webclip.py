"""``rootpilot install-icon``: put a RootPilot icon on the home screen.

The icon is an iOS web clip: a bundle in ``/var/mobile/Library/WebClips`` with an Info.plist
and an icon. Tapping it opens the loopback control page full-screen. Nothing is signed or
compiled; ``uicache`` makes SpringBoard pick it up.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

WEBCLIPS = Path("/var/mobile/Library/WebClips")
# iOS names web clips with a 32-character hex identifier; derive a stable one.
CLIP_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "control.rootpilot").hex.upper()
BUNDLE_NAME = f"{CLIP_ID}.webclip"
LEGACY_NAMES = ("com.rootpilot.control.webclip",)
ICON = Path(__file__).resolve().parent / "webui" / "icon.png"


def install_webclip(url: str, root: Path = WEBCLIPS, title: str = "RootPilot") -> Path:
    _remove_legacy(root)
    bundle = root / BUNDLE_NAME
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, bundle / "icon.png")
    info = {
        "Title": title,
        "URL": url,
        "FullScreen": True,  # opens like an app, without Safari's address bar
        "IsAppClip": False,
        "IgnoreManifestScope": False,
        "Orientations": 0,
        "RemovalDisallowed": False,
        "IconIsPrecomposed": True,
        "IconIsScreenShotBased": False,
        "ClassicMode": False,
    }
    with open(bundle / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)
    return bundle


def _remove_legacy(root: Path) -> bool:
    removed = False
    for name in LEGACY_NAMES:
        if (root / name).exists():
            shutil.rmtree(root / name)
            removed = True
    return removed


def remove_webclip(root: Path = WEBCLIPS) -> bool:
    removed = _remove_legacy(root)
    bundle = root / BUNDLE_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
        removed = True
    return removed


def refresh_icons() -> Optional[str]:
    """Run uicache so SpringBoard shows the change; returns an error message or None."""
    tool = shutil.which("uicache")
    if tool is None:
        return "uicache not found; respring to see the icon"
    result = subprocess.run([tool], capture_output=True, text=True, timeout=120, check=False)
    return None if result.returncode == 0 else (result.stderr.strip() or "uicache failed")
