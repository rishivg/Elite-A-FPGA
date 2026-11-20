#!/usr/bin/env python3
"""Compose build/sim/ship_*.pbm frames into a single grid PNG. Pure stdlib.

Usage:  python3 tools/montage.py [glob] [out.png] [scale] [cols]
"""

from __future__ import annotations

import glob
import sys

if __package__:
    from .pbm2png import read_pbm, write_grayscale_png
else:
    from pbm2png import read_pbm, write_grayscale_png


def main() -> None:
    pat = sys.argv[1] if len(sys.argv) > 1 else "build/sim/ship_*.pbm"
    out = sys.argv[2] if len(sys.argv) > 2 else "build/sim/ship_montage.png"
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    cols = int(sys.argv[4]) if len(sys.argv) > 4 else 4

    files = sorted(glob.glob(pat))
    if not files:
        sys.exit(f"no frames matched {pat}")
    W, H, _ = read_pbm(files[0])
    rows = (len(files) + cols - 1) // cols
    cellW, cellH, gap = W * scale, H * scale, 6
    imgW = cols * cellW + (cols + 1) * gap
    imgH = rows * cellH + (rows + 1) * gap
    canvas = [[200] * imgW for _ in range(imgH)]  # gray gutters

    for i, f in enumerate(files):
        w, h, px = read_pbm(f)
        ox = gap + (i % cols) * (cellW + gap)
        oy = gap + (i // cols) * (cellH + gap)
        for y in range(cellH):
            for x in range(cellW):
                canvas[oy + y][ox + x] = 255 if px[(y // scale) * w + x // scale] else 0

    write_grayscale_png(out, imgW, imgH, canvas)
    print(f"wrote {out} ({imgW}x{imgH}) from {len(files)} frames")


if __name__ == "__main__":
    main()
