#!/usr/bin/env python3
"""Stitch PBM frames into a looping animated GIF (white wireframe on black).

Usage:  python3 tools/gif.py "build/sim/soc_ship_*.pbm" build/sim/soc_ship.gif [scale] [ms]
"""

import glob
import sys
from contextlib import ExitStack

from PIL import Image

if __package__:
    from .pbm2png import read_pbm
else:
    from pbm2png import read_pbm


def main() -> None:
    pat = sys.argv[1] if len(sys.argv) > 1 else "build/sim/soc_ship_*.pbm"
    out = sys.argv[2] if len(sys.argv) > 2 else "build/sim/soc_ship.gif"
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    ms = int(sys.argv[4]) if len(sys.argv) > 4 else 80

    files = sorted(glob.glob(pat))
    if not files:
        sys.exit(f"no frames matched {pat}")

    with ExitStack() as stack:
        frames = []
        for f in files:
            w, h, px = read_pbm(f)
            with Image.new("L", (w, h)) as image:
                image.putdata([255 if pixel else 0 for pixel in px])
                frames.append(
                    stack.enter_context(image.resize((w * scale, h * scale), Image.NEAREST))
                )

        frames[0].save(
            out, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=False
        )
        print(
            f"wrote {out} ({len(frames)} frames, {frames[0].size[0]}x{frames[0].size[1]}, {ms}ms/frame)"
        )


if __name__ == "__main__":
    main()
