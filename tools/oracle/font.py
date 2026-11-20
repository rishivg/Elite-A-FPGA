"""Check the 96 firmware glyphs, character bounds and space/digit patterns."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

_FONT_RE = re.compile(
    r"static\s+const\s+unsigned\s+char\s+font8x8\s*\[\s*96\s*\]\s*\[\s*8\s*\]\s*=\s*\{(.*?)\n\};",
    re.DOTALL,
)
# Accept either `0xHH` (annotated glyphs at the top of the table) or bare `0`
# (the trailing zero-glyphs for the unused 0x60..0x7F lowercase range).
_GLYPH_RE = re.compile(r"\{\s*((?:(?:0x[0-9A-Fa-f]+|0)\s*,\s*){7}(?:0x[0-9A-Fa-f]+|0))\s*\}")


def parse_fw_font() -> list[list[int]]:
    text = FW_PATH.read_text()
    m = _FONT_RE.search(text)
    if not m:
        raise ValueError("font8x8[96][8] table not found in firmware source")
    body = m.group(1)
    glyphs = []
    for g in _GLYPH_RE.finditer(body):
        bytes8 = [int(s.strip(), 0) for s in g.group(1).split(",")]
        if len(bytes8) != 8:
            raise ValueError(f"glyph {len(glyphs)} not 8 bytes: {bytes8}")
        glyphs.append(bytes8)
    return glyphs


def check() -> tuple[bool, str]:
    try:
        font = parse_fw_font()
    except Exception as e:
        return False, f"parse failed: {e}"

    if len(font) != 96:
        return False, f"expected 96 glyphs, got {len(font)}"

    # 1. Space (0x20, index 0) is all zeros.
    if any(b != 0 for b in font[0]):
        return False, f"space glyph (0x20) not empty: {font[0]}"

    # 2. Each glyph byte fits in 8 bits.
    for i, g in enumerate(font):
        for j, b in enumerate(g):
            if not (0 <= b <= 0xFF):
                return False, f"glyph 0x{0x20 + i:02X} byte {j} out of byte range: {b}"

    # 3. Digits 0-9 (0x30..0x39) and uppercase A-Z (0x41..0x5A) are non-empty.
    def glyph_lit_bits(glyph):
        return sum(bin(byte).count("1") for byte in glyph)

    for c in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)):
        n = glyph_lit_bits(font[c - 0x20])
        if n < 4:
            return False, f"glyph 0x{c:02X} ('{chr(c)}') only has {n} set bits"

    # 4. Spot-check a few obvious shapes: '0' should be wider/denser than '1'.
    n0 = glyph_lit_bits(font[0x30 - 0x20])
    n1 = glyph_lit_bits(font[0x31 - 0x20])
    if n0 <= n1:
        return False, f"'0' ({n0} bits) should be heavier than '1' ({n1} bits)"

    total_bits = sum(glyph_lit_bits(g) for g in font)
    return True, (
        f"96 glyphs parsed; space empty; digits + A-Z all populated "
        f"(min 4 set bits); '0' heavier than '1'; total {total_bits} lit bits"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "font: " + msg)
