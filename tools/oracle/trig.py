"""Check the firmware sine table against 64 Q8.8 samples of a full turn."""

from __future__ import annotations

import math
import re
from pathlib import Path

from . import spec

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"


def reference_sintab() -> list[int]:
    """Canonical Q8.8 sine table — what the firmware *should* contain."""
    out = []
    for k in range(64):
        v = round(spec.ONE * math.sin(2.0 * math.pi * k / 64.0))
        # clamp the +1.0 corner to the table value the firmware uses (256)
        if v > spec.ONE:
            v = spec.ONE
        if v < -spec.ONE:
            v = -spec.ONE
        out.append(v)
    return out


def isin(k: int, tab: list[int] | None = None) -> int:
    t = tab or reference_sintab()
    return t[k & 63]


def icos(k: int, tab: list[int] | None = None) -> int:
    return isin(k + 16, tab)


_SINTAB_RE = re.compile(
    r"static\s+const\s+short\s+sintab\s*\[\s*64\s*\]\s*=\s*\{(?P<body>[^}]*)\};",
    re.DOTALL,
)


def parse_fw_sintab(src: str | None = None) -> list[int]:
    """Pull the sintab[] literal out of fw/main.c and return as a list of 64 ints."""
    text = src if src is not None else FW_PATH.read_text()
    m = _SINTAB_RE.search(text)
    if not m:
        raise ValueError("sintab[64] literal not found in firmware source")
    nums = [int(t) for t in re.findall(r"-?\d+", m.group("body"))]
    if len(nums) != 64:
        raise ValueError(f"sintab[] had {len(nums)} entries, expected 64")
    return nums


def check() -> tuple[bool, str]:
    """Static check: firmware sintab equals reference."""
    ref = reference_sintab()
    try:
        fw = parse_fw_sintab()
    except Exception as e:
        return False, f"could not parse firmware sintab: {e}"
    diffs = [(k, fw[k], ref[k]) for k in range(64) if fw[k] != ref[k]]
    if diffs:
        first = diffs[0]
        return False, (
            f"{len(diffs)} entries differ — first at k={first[0]}: "
            f"fw={first[1]} expected={first[2]}"
        )
    # Bonus: confirm Pythagoras-ish identity to within Q8.8 quantisation.
    worst = max(
        abs(fw[k] * fw[k] + fw[(k + 16) & 63] * fw[(k + 16) & 63] - spec.ONE * spec.ONE)
        for k in range(64)
    )
    worst_pct = worst * 100.0 / (spec.ONE * spec.ONE)
    return True, f"64 entries match reference; max |sin^2+cos^2 - 1.0| = {worst_pct:.2f}%"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "trig: " + msg)
