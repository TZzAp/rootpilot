from __future__ import annotations

import random
import struct
import zlib

import pytest

from agent.errors import StaleRefError
from agent.perception import png
from agent.perception.axtree import hit_test, render, secure_focused
from agent.perception.image import downscale, png_size
from agent.types import Image, Rect, UINode

# ---- PNG codec -----------------------------------------------------------------------


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _filtered_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode RGBA using every filter type in turn, to exercise the decoder's unfilter paths."""
    bpp, stride = 4, width * 4
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = rgba[y * stride : (y + 1) * stride]
        ftype = y % 5
        out = bytearray()
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up_left = prev[i - bpp] if i >= bpp else 0
            pred = (0, left, prev[i], (left + prev[i]) >> 1, _paeth(left, prev[i], up_left))[ftype]
            out.append((line[i] - pred) & 0xFF)
        raw += bytes([ftype]) + out
        prev = line

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def test_png_decode_all_filter_types() -> None:
    rnd = random.Random(1)
    width, height = 7, 10
    rgba = bytes(rnd.randrange(256) for _ in range(width * height * 4))
    img = png.decode(_filtered_png(width, height, rgba))
    expected = bytes(b for i, b in enumerate(rgba) if i % 4 != 3)
    assert (img.width, img.height) == (width, height)
    assert img.pixels == expected


def test_png_roundtrip_and_resize() -> None:
    src = png.RGBImage(4, 2, bytes(range(24)))
    assert png.decode(png.encode(src)) == src
    small = png.resize(src, 2, 1)
    assert (small.width, small.height) == (2, 1)
    assert small.pixels == bytes([0, 1, 2, 6, 7, 8])


def test_png_rejects_garbage() -> None:
    with pytest.raises(png.PNGError):
        png.decode(b"not a png")


def test_downscale_keeps_scale_consistent() -> None:
    data = png.solid(1125, 2436)
    image = Image(png=data, width=1125, height=2436, scale=3.0)
    small = downscale(image, 1280)
    assert max(small.width, small.height) == 1280
    assert png_size(small.png) == (small.width, small.height)
    # pixels per point shrinks by the same ratio as the image
    assert small.scale == pytest.approx(3.0 * 1280 / 2436)
    assert downscale(small, 1280) is small


# ---- AX tree rendering ---------------------------------------------------------------


def _settings_tree() -> UINode:
    return UINode(
        role="Window",
        frame=Rect(0, 0, 375, 812),
        children=[
            UINode(role="NavigationBar", label="Settings", frame=Rect(0, 44, 375, 52)),
            UINode(
                role="Table",
                frame=Rect(0, 96, 375, 716),
                children=[
                    UINode(role="Cell", label="General", frame=Rect(0, 380, 375, 52)),
                    UINode(role="Cell", label="Hidden", frame=Rect(0, 432, 375, 52), visible=False),
                    UINode(role="Switch", label="Wi-Fi", value="1", frame=Rect(300, 200, 51, 31)),
                    UINode(role="Group", frame=Rect(0, 0, 1, 1)),
                ],
            ),
            UINode(
                role="TextField",
                label="Password",
                value="hunter2",
                secure=True,
                focused=True,
                frame=Rect(20, 600, 335, 44),
            ),
        ],
    )


def test_render_format_and_refs() -> None:
    rendered = render(_settings_tree())
    lines = rendered.text.splitlines()
    assert lines[0] == 'e1 NavigationBar "Settings" @(188,70)'
    assert 'e2 Cell "General" @(188,406)' in lines
    assert 'e3 Switch "Wi-Fi" = "1" @(326,216)' in lines
    assert rendered.resolve("e2").label == "General"
    # hidden nodes and unlabeled containers produce no lines
    assert "Hidden" not in rendered.text
    assert "Group" not in rendered.text and "Table" not in rendered.text


def test_render_never_shows_secure_values() -> None:
    rendered = render(_settings_tree())
    assert "hunter2" not in rendered.text
    assert 'e4 SecureTextField "Password" @(188,622) [secure,focused]' in rendered.text
    assert secure_focused(_settings_tree())


def test_render_truncates_and_rejects_stale_refs() -> None:
    root = UINode(
        role="Window",
        frame=Rect(0, 0, 10, 10),
        children=[UINode(role="Button", label=f"b{i}", frame=Rect(0, i, 10, 1)) for i in range(10)],
    )
    rendered = render(root, max_elements=3)
    assert rendered.text.splitlines()[-1] == "… 7 more elements"
    with pytest.raises(StaleRefError):
        rendered.resolve("e9")


def test_hit_test_returns_deepest_element() -> None:
    node = hit_test(_settings_tree(), 100, 400)
    assert node is not None and node.label == "General"
    assert hit_test(_settings_tree(), 5, 5) is None
