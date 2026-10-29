#!/usr/bin/env python3
"""Rebuild a prebuilt ZXTouch .deb so it listens on 127.0.0.1 instead of 0.0.0.0.

Runs on a dev host with dpkg-deb. Patches the bind address in every slice of pccontrol.dylib
("0.0.0.0" -> "127.1", which inet_addr() parses as 127.0.0.1 and which fits in the same
bytes), removes the postinst line that gives ``mobile`` passwordless root for zxtouchb, and
repacks with root ownership. The patched dylib must be re-signed on the device afterwards
(``sudo ldid -S``), see docs/SETUP.md.

Usage: python3 scripts/zxtouch_loopback.py com.zjx.ioscontrol_0.0.7-10_iphoneos-arm.deb
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DYLIB = "Library/MobileSubstrate/DynamicLibraries/pccontrol.dylib"
OLD, NEW = b"0.0.0.0\x00", b"127.1\x00\x00\x00"
SUDOERS_LINE = 'echo "mobile ALL=(root) NOPASSWD:/usr/bin/zxtouchb" >> /etc/sudoers\n'


def main(src: str) -> int:
    dpkg_deb = shutil.which("dpkg-deb")
    if dpkg_deb is None:
        print("dpkg-deb not found (apt install dpkg)", file=sys.stderr)
        return 1
    deb = Path(src).resolve()
    out = deb.with_name(deb.stem.replace("_iphoneos-arm", "") + "+loopback_iphoneos-arm.deb")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        subprocess.run([dpkg_deb, "-R", str(deb), str(root)], check=True)

        dylib = root / DYLIB
        data = dylib.read_bytes()
        count = data.count(OLD)
        if count == 0:
            print("no 0.0.0.0 bind address found; is this ZXTouch 0.0.7?", file=sys.stderr)
            return 1
        dylib.write_bytes(data.replace(OLD, NEW))
        print(f"patched bind address in {count} architecture slice(s)")

        postinst = root / "DEBIAN" / "postinst"
        if postinst.exists():
            text = postinst.read_text()
            if SUDOERS_LINE in text:
                postinst.write_text(text.replace(SUDOERS_LINE, "# removed: sudoers entry\n"))
                print("removed passwordless-root sudoers line from postinst")

        control = root / "DEBIAN" / "control"
        lines = [
            line + "+loopback" if line.startswith("Version:") else line
            for line in control.read_text().splitlines()
        ]
        control.write_text("\n".join(lines) + "\n")

        subprocess.run(
            [dpkg_deb, "--root-owner-group", "-Zxz", "-b", str(root), str(out)], check=True
        )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
