#!/usr/bin/env python3
"""Turn frames from ``rootpilot capture`` into a compact README GIF (dev tool; needs Pillow).

Frames that barely differ from the previous kept frame are dropped (the screen is static while
the model thinks), long pauses are capped, and the rest play back faster than real time.

Usage: python scripts/frames_to_gif.py FRAMES_DIR OUT.gif [--width 320] [--speed 2.5] [--fps 4]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


def changed(a: Image.Image, b: Image.Image, threshold: float) -> bool:
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    return ImageStat.Stat(diff).mean[0] > threshold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--speed", type=float, default=2.5, help="playback speed-up")
    ap.add_argument("--fps", type=float, default=4.0, help="capture rate of the frames")
    ap.add_argument("--start", type=int, default=1, help="first frame number to use")
    ap.add_argument("--end", type=int, default=0, help="last frame number to use (0 = all)")
    ap.add_argument("--threshold", type=float, default=0.6, help="min mean pixel change to keep")
    ap.add_argument("--hold", type=float, default=1.5, help="seconds to hold the last frame")
    ap.add_argument(
        "--max-frame", type=float, default=0.9, help="cap on any one frame's display time (s)"
    )
    args = ap.parse_args()

    files = sorted(args.frames.glob("*.jpg"))[args.start - 1 : args.end or None]
    if not files:
        raise SystemExit(f"no frames in {args.frames}")
    frame_ms = 1000 / args.fps / args.speed
    kept: list[Image.Image] = []
    durations: list[float] = []
    for f in files:
        img = Image.open(f).convert("RGB")
        img = img.resize((args.width, round(img.height * args.width / img.width)), Image.LANCZOS)
        if kept and not changed(kept[-1], img, args.threshold):
            durations[-1] += frame_ms  # extend the previous frame instead of repeating it
            continue
        kept.append(img)
        durations.append(frame_ms)
    durations = [min(d, args.max_frame * 1000) for d in durations]
    durations[-1] += args.hold * 1000
    # A shared adaptive palette keeps colours stable between frames (no flicker).
    palette = kept[len(kept) // 2].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = [k.quantize(palette=palette, dither=Image.Dither.NONE) for k in kept]
    frames[0].save(
        args.out,
        save_all=True,
        append_images=frames[1:],
        duration=[max(20, round(d)) for d in durations],
        loop=0,
        optimize=True,
        disposal=1,
    )
    size_kb = args.out.stat().st_size // 1024
    total = sum(durations) / 1000
    print(f"{args.out}: {len(frames)}/{len(files)} frames, {total:.1f} s, {size_kb} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
