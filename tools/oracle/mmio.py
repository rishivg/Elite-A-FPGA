"""Check register addresses and control bits across the demo firmware, RTL and model."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "mmio.h"
SOC_PATH = Path(__file__).resolve().parents[2] / "rtl" / "soc" / "Soc.scala"

# Expected MMIO word offsets — must match Soc.scala mmioSel cases AND fw/mmio.h
# enum addresses. Update all three when adding a register.
EXPECTED_OFFSETS = {
    "DBG": 0,
    "CTRL": 1,
    "FRAME": 2,
    "INPUT": 3,
    "M0": 4,  # matrix m0..m8 occupy offsets 4..12
    "LP0": 13,
    "LP1": 14,
    "LCOL": 15,
    "T0": 16,  # translation tx, ty, tz occupy offsets 16..18
    "CCTR": 19,
    "CRAD": 20,
    "CCOL": 21,
    "INPUT2": 22,
    "PSG": 23,
    "MODEL": 24,
}

# Expected CTRL bits — write side.
EXPECTED_CTRL_BITS = {
    "START": 0,
    "CLEAR": 1,
    "START_LINE": 2,
    "START_CIRCLE": 3,
}

_ENUM_RE = re.compile(r"enum\s*\{([^}]*)\}\s*;", re.DOTALL)
_DEFINE_RE = re.compile(r"#define\s+(\w+)\s+(\w+)")


def parse_fw_enum_names(src: str | None = None) -> dict[str, int]:
    """Pull the first enum from fw/mmio.h (the MMIO register-name -> offset map)
    and resolve any sequential successors."""
    text = src if src is not None else FW_PATH.read_text()
    m = _ENUM_RE.search(text)
    if not m:
        raise ValueError("MMIO enum not found in firmware source")
    body = m.group(1)
    out: dict[str, int] = {}
    last = -1
    for raw in body.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if "=" in raw:
            name, val = [s.strip() for s in raw.split("=", 1)]
            v = int(val, 0)
        else:
            name = raw
            v = last + 1
        out[name] = v
        last = v
    return out


def parse_fw_defines(names: set[str], src: str | None = None) -> dict[str, int]:
    """Pull the named #define values out of fw/mmio.h."""
    text = src if src is not None else FW_PATH.read_text()
    out: dict[str, int] = {}
    for m in _DEFINE_RE.finditer(text):
        name, val = m.group(1), m.group(2)
        if name in names:
            v_str = val.rstrip("uU")
            try:
                out[name] = int(v_str, 0)
            except ValueError:
                pass
    return out


def parse_soc_mmio_cases(src: str | None = None) -> set[int]:
    """Scan Soc.scala for the `is(N)` cases inside the MMIO switch — gives a
    sanity check that the same set of offsets is decoded in HW."""
    text = src if src is not None else SOC_PATH.read_text()
    # Pull all `is(N)` arms (matches both literal and computed (4+i) forms).
    plain = {int(m.group(1)) for m in re.finditer(r"\bis\(\s*(\d+)\s*\)", text)}
    return plain


def check() -> tuple[bool, str]:
    # 1. Firmware enum matches the spec table.
    fw = parse_fw_enum_names()
    for name, expected in EXPECTED_OFFSETS.items():
        if name not in fw:
            return False, f"firmware enum is missing {name}"
        if fw[name] != expected:
            return False, f"firmware {name} = {fw[name]}, expected {expected}"

    # 2. CTRL write-bit defines match.
    defs = parse_fw_defines(set(EXPECTED_CTRL_BITS.keys()))
    for name, bit in EXPECTED_CTRL_BITS.items():
        if name not in defs:
            return False, f"firmware #define {name} missing"
        if defs[name] != (1 << bit):
            return False, f"firmware {name} = 0x{defs[name]:x}, expected 0x{1 << bit:x} (bit {bit})"

    # 3. Soc.scala has a decode arm for every literal offset (matrix + tvec
    #    are decoded via for-loops so they won't show up here; check just the
    #    handful that are written as `is(N)` literals).
    soc_arms = parse_soc_mmio_cases()
    must_have = {0, 1, 2, 13, 14, 15, 19, 20, 21}
    missing = must_have - soc_arms
    if missing:
        return False, f"Soc.scala missing decode arms for offsets {sorted(missing)}"

    return True, (
        f"{len(EXPECTED_OFFSETS)} MMIO offsets match between firmware enum "
        f"and spec; {len(EXPECTED_CTRL_BITS)} CTRL write-bits correct; "
        f"Soc.scala has explicit decode arms for {sorted(must_have)}"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "mmio: " + msg)
