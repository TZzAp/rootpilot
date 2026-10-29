#!/usr/bin/env python3
"""Rebuild a prebuilt ZXTouch .deb so it listens on 127.0.0.1 instead of 0.0.0.0.

Runs on a dev host with dpkg-deb. Patches the bind address in every slice of pccontrol.dylib
("0.0.0.0" -> "127.1", which inet_addr() parses as 127.0.0.1 and which fits in the same
bytes), removes the postinst line that gives ``mobile`` passwordless root for zxtouchb, and
repacks with root ownership. The patched dylib must be re-signed on the device afterwards
(``sudo ldid -S``), see docs/SETUP.md.

With ``--rootless`` it instead builds a minimal rootless package (Dopamine, palera1n rootless):
only the tweak and its data folder, installed under /var/jb, with the tweak's substrate
load commands pointed at ``@rpath/libsubstrate.dylib`` and an LC_RPATH of /var/jb/usr/lib.
The ZXTouch app, its bundled Python 3.7 and helper binaries are left out; RootPilot only
needs the tweak.

Usage: python3 scripts/zxtouch_loopback.py [--rootless] com.zjx.ioscontrol_0.0.7-10_iphoneos-arm.deb
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

DYLIB = "Library/MobileSubstrate/DynamicLibraries/pccontrol.dylib"
FILTER = "Library/MobileSubstrate/DynamicLibraries/pccontrol.plist"
OLD, NEW = b"0.0.0.0\x00", b"127.1\x00\x00\x00"
SUDOERS_LINE = 'echo "mobile ALL=(root) NOPASSWD:/usr/bin/zxtouchb" >> /etc/sudoers\n'

SUBSTRATE_PATHS = {
    b"/usr/lib/libsubstrate.dylib",
    b"/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate",
}
RPATH_SUBSTRATE = b"@rpath/libsubstrate.dylib"
ROOTLESS_RPATH = b"/var/jb/usr/lib"
LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_RPATH, LC_SEGMENT_64 = 0xC, 0x80000018, 0x8000001C, 0x19


def _patch_slice(b: bytearray, off: int) -> int:
    """Rootless-patch one 64-bit Mach-O slice in place; returns how many dylib paths changed."""
    ncmds, sizeofcmds = struct.unpack_from("<II", b, off + 16)
    p, end, changed, first_sect, has_rpath = off + 32, off + 32 + sizeofcmds, 0, None, False
    while p < end:
        cmd, cmdsize = struct.unpack_from("<II", b, p)
        if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB):
            name_off = struct.unpack_from("<I", b, p + 8)[0]
            raw = bytes(b[p + name_off : p + cmdsize])
            name = raw.split(b"\0", 1)[0]
            if name in SUBSTRATE_PATHS:
                if len(RPATH_SUBSTRATE) + 1 > cmdsize - name_off:
                    raise ValueError(f"no room for {RPATH_SUBSTRATE!r} in {name!r}")
                b[p + name_off : p + cmdsize] = RPATH_SUBSTRATE.ljust(cmdsize - name_off, b"\0")
                changed += 1
        elif cmd == LC_RPATH:
            path_off = struct.unpack_from("<I", b, p + 8)[0]
            if bytes(b[p + path_off : p + cmdsize]).split(b"\0", 1)[0] == ROOTLESS_RPATH:
                has_rpath = True
        elif cmd == LC_SEGMENT_64:
            nsects = struct.unpack_from("<I", b, p + 64)[0]
            for s in range(nsects):
                fileoff = struct.unpack_from("<I", b, p + 72 + s * 80 + 48)[0]
                if fileoff and (first_sect is None or fileoff < first_sect):
                    first_sect = fileoff
        p += cmdsize
    if changed and not has_rpath:
        size = (12 + len(ROOTLESS_RPATH) + 1 + 7) // 8 * 8
        if first_sect is None or 32 + sizeofcmds + size > first_sect:
            raise ValueError("no header space for LC_RPATH")
        rpath = struct.pack("<III", LC_RPATH, size, 12) + ROOTLESS_RPATH
        b[end : end + size] = rpath.ljust(size, b"\0")
        struct.pack_into("<II", b, off + 16, ncmds + 1, sizeofcmds + size)
    return changed


def rootless_patch(data: bytes) -> bytes:
    """Patch every 64-bit slice of a fat or thin Mach-O; 32-bit slices are left alone."""
    b = bytearray(data)
    if struct.unpack_from(">I", b, 0)[0] == 0xCAFEBABE:
        nfat = struct.unpack_from(">I", b, 4)[0]
        offsets = [struct.unpack_from(">I", b, 8 + i * 20 + 8)[0] for i in range(nfat)]
    else:
        offsets = [0]
    changed = sum(
        _patch_slice(b, off) for off in offsets if struct.unpack_from("<I", b, off)[0] == 0xFEEDFACF
    )
    if not changed:
        raise ValueError("no substrate load commands found")
    return bytes(b)


def _rootless_tree(src: Path, dst: Path, version: str) -> None:
    jb = dst / "var" / "jb"
    for rel in (DYLIB, FILTER):
        (jb / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, jb / rel)
    (jb / DYLIB).write_bytes(rootless_patch((jb / DYLIB).read_bytes()))
    data = src / "var" / "mobile" / "Library" / "ZXTouch"
    if data.exists():
        shutil.copytree(data, dst / "var" / "mobile" / "Library" / "ZXTouch")
    debian = dst / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(
        "Package: com.zjx.ioscontrol\n"
        "Name: ZXTouch (tweak only, rootless, loopback)\n"
        f"Version: {version}\n"
        "Architecture: iphoneos-arm64\n"
        "Description: ZXTouch touch/OCR tweak, bound to 127.0.0.1, for RootPilot\n"
        "Maintainer: RootPilot\nAuthor: Jason Z\nSection: Tweaks\n"
    )
    postinst = debian / "postinst"
    postinst.write_text(
        "#!/bin/sh\nchown -R 501:501 /var/mobile/Library/ZXTouch 2>/dev/null\nexit 0\n"
    )
    postinst.chmod(0o755)


def main(argv: list[str]) -> int:
    rootless = "--rootless" in argv
    args = [a for a in argv if a != "--rootless"]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    dpkg_deb = shutil.which("dpkg-deb")
    if dpkg_deb is None:
        print("dpkg-deb not found (apt install dpkg)", file=sys.stderr)
        return 1
    deb = Path(args[0]).resolve()
    stem = deb.stem.replace("_iphoneos-arm", "")
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

        if rootless:
            version = next(
                line.split(":", 1)[1].strip()
                for line in (root / "DEBIAN" / "control").read_text().splitlines()
                if line.startswith("Version:")
            )
            out = deb.with_name(f"{stem}+loopback+rootless_iphoneos-arm64.deb")
            pkg = Path(tmp) / "rootless"
            _rootless_tree(root, pkg, f"{version}+loopback+rootless")
            print("rootless: tweak only, substrate via @rpath, LC_RPATH /var/jb/usr/lib")
            root = pkg
        else:
            out = deb.with_name(f"{stem}+loopback_iphoneos-arm.deb")
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
    raise SystemExit(main(sys.argv[1:]))
