#!/usr/bin/env python3
"""Convert an ASCII PBM (P1) bitmap to a scaled grayscale PNG. Pure stdlib.

Usage:  python3 tools/pbm2png.py build/sim/lines.pbm build/sim/lines.png [scale]
"""

from __future__ import annotations

import struct
import sys
import zlib
from collections.abc import Iterable
from pathlib import Path


def read_pbm(path: str) -> tuple[int, int, list[int]]:
    toks = Path(path).read_text().split()
    assert toks[0] == "P1", "expected an ASCII PBM (P1)"
    w, h = int(toks[1]), int(toks[2])
    px = [1 if c == "1" else 0 for c in toks[3:]][: w * h]
    return w, h, px


def write_png(path: str, w: int, h: int, px: list[int], scale: int = 8) -> None:
    W, H = w * scale, h * scale
    rows = (
        bytes(0 if px[(y // scale) * w + x // scale] else 255 for x in range(W)) for y in range(H)
    )
    write_grayscale_png(path, W, H, rows)


def write_grayscale_png(path: str, width: int, height: int, rows: Iterable[Iterable[int]]) -> None:
    """Encode 8-bit grayscale rows using PNG's unfiltered lossless format."""
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    w, h, px = read_pbm(sys.argv[1])
    write_png(sys.argv[2], w, h, px, scale)
    print(f"{sys.argv[1]} -> {sys.argv[2]}  ({w * scale}x{h * scale}, scale {scale})")
