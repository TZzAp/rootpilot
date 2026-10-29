#!/usr/bin/env python3
"""Generate agent/webui/icon.png, the RootPilot home-screen icon (no Pillow needed).

A navy gradient with a bright crosshair — an agent that aims and taps. iOS rounds the corners,
so the icon is a plain square.
"""

from __future__ import annotations

import math
from pathlib import Path

from agent.perception.png import RGBImage, encode

SIZE = 180


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def build() -> RGBImage:
    top, bottom = (11, 24, 48), (18, 58, 107)
    accent, white = (77, 163, 255), (233, 239, 245)
    cx = cy = SIZE / 2
    px = bytearray(SIZE * SIZE * 3)
    for y in range(SIZE):
        base = _mix(top, bottom, y / SIZE)
        for x in range(SIZE):
            r = math.hypot(x - cx, y - cy)
            ang = math.atan2(y - cy, x - cx)
            col = base
            # ring at radius ~62 px, 5 px thick
            if abs(r - 62) < 2.6:
                col = white
            # four crosshair ticks crossing the ring
            if r < 78 and r > 46 and min(abs(math.cos(ang)), abs(math.sin(ang))) < 0.08:
                col = white
            # center dot
            if r < 11:
                col = accent
            i = (y * SIZE + x) * 3
            px[i : i + 3] = bytes(col)
    return RGBImage(SIZE, SIZE, bytes(px))


def main() -> int:
    out = Path(__file__).resolve().parent.parent / "agent" / "webui" / "icon.png"
    out.write_bytes(encode(build()))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
