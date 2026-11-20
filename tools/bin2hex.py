#!/usr/bin/env python3
"""Convert a flat binary into little-endian 32-bit hex words (one per line),
suitable for loading into a SpinalHDL Mem.

Usage:  python3 tools/bin2hex.py firmware.bin firmware.hex
"""

import sys
from pathlib import Path


def main(src: str, dst: str) -> None:
    data = Path(src).read_bytes()
    data += bytes(-len(data) % 4)
    with open(dst, "w") as f:
        for i in range(0, len(data), 4):
            word = int.from_bytes(data[i : i + 4], "little")
            f.write(f"{word:08x}\n")
    print(f"{src} -> {dst}  ({len(data)} bytes, {len(data) // 4} words)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
