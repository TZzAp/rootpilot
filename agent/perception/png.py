"""Minimal pure-Python PNG codec (8-bit, non-interlaced), so the device needs no Pillow.

Decoding a full-resolution iPhone screenshot this way takes a few seconds on-device; the M2
helper will hand over downscaled images directly and make this the fallback path.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from agent.errors import RootPilotError

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PNGError(RootPilotError):
    pass


@dataclass
class RGBImage:
    """Raw 8-bit RGB pixels, row-major, 3 bytes per pixel."""

    width: int
    height: int
    pixels: bytes


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(_SIGNATURE):
        raise PNGError("not a PNG file")
    out: list[tuple[bytes, bytes]] = []
    pos = len(_SIGNATURE)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        out.append((kind, data[pos + 8 : pos + 8 + length]))
        pos += 12 + length
        if kind == b"IEND":
            break
    return out


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif ftype != 0:
            raise PNGError(f"bad filter type {ftype}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return out


def decode(data: bytes) -> RGBImage:
    width = height = depth = ctype = interlace = 0
    palette = b""
    idat = bytearray()
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat += body
    if depth != 8 or ctype not in _CHANNELS:
        raise PNGError(f"unsupported PNG (bit depth {depth}, color type {ctype})")
    if interlace:
        raise PNGError("interlaced PNGs are not supported")
    bpp = _CHANNELS[ctype]
    px = _unfilter(zlib.decompress(bytes(idat)), width, height, bpp)

    if ctype == 2:
        rgb = bytes(px)
    elif ctype == 6:
        rgb = bytes(b for i, b in enumerate(px) if i % 4 != 3)
    elif ctype == 0:
        rgb = bytes(v for g in px for v in (g, g, g))
    elif ctype == 4:
        rgb = bytes(v for g in px[0::2] for v in (g, g, g))
    else:
        rgb = b"".join(palette[i * 3 : i * 3 + 3] for i in px)
    return RGBImage(width, height, rgb)


def encode(img: RGBImage) -> bytes:
    stride = img.width * 3
    raw = b"".join(b"\x00" + img.pixels[y * stride : (y + 1) * stride] for y in range(img.height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0)
    return (
        _SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def resize(img: RGBImage, width: int, height: int) -> RGBImage:
    """Nearest-neighbour resize; good enough for UI screenshots the model reads."""
    out = bytearray(width * height * 3)
    xs = [min(img.width - 1, int(x * img.width / width)) * 3 for x in range(width)]
    src_stride = img.width * 3
    for y in range(height):
        sy = min(img.height - 1, int(y * img.height / height)) * src_stride
        row = y * width * 3
        for x, sx in enumerate(xs):
            out[row + x * 3 : row + x * 3 + 3] = img.pixels[sy + sx : sy + sx + 3]
    return RGBImage(width, height, bytes(out))


def solid(width: int, height: int, rgb: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    return encode(RGBImage(width, height, bytes(rgb) * (width * height)))
