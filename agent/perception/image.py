"""Downscale screenshots before they go to a model (SPEC §3.3)."""

from __future__ import annotations

import io
import struct

from agent.perception import png
from agent.types import Image


def png_size(data: bytes) -> tuple[int, int]:
    """Read width/height from the IHDR chunk without decoding pixels."""
    if len(data) < 24 or not data.startswith(b"\x89PNG"):
        raise png.PNGError("not a PNG file")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def downscale(image: Image, max_edge: int) -> Image:
    """Shrink so the long edge is at most ``max_edge`` pixels; ``scale`` is kept consistent."""
    long_edge = max(image.width, image.height)
    if long_edge <= max_edge:
        return image
    ratio = max_edge / long_edge
    width = max(1, round(image.width * ratio))
    height = max(1, round(image.height * ratio))
    data = _resize_with_pillow(image.png, width, height)
    if data is None:
        small = png.resize(png.decode(image.png), width, height)
        data = png.encode(small)
    return Image(png=data, width=width, height=height, scale=image.scale * ratio)


def _resize_with_pillow(data: bytes, width: int, height: int) -> bytes | None:
    # Pillow is optional: used only when it's already importable (e.g. on a dev host).
    try:
        from PIL import Image as PILImage  # type: ignore[import-not-found, unused-ignore]
    except ImportError:
        return None
    with PILImage.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.convert("RGB").resize((width, height), PILImage.LANCZOS).save(out, format="PNG")
        return out.getvalue()
