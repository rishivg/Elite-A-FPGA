"""Generate demo system names from the firmware digram table and galaxy seeds."""

from __future__ import annotations

import re
from pathlib import Path

from . import galaxy

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

# The classic Elite digram table (32 pairs), expected to be unchanged in fw.
_CLASSIC = "ALLEXEGEZACEBISOUSESARMAINDIREA?ERATENBERALAVETIEDORQUANTEISRION"


def parse_digrams() -> str:
    """Pull the DIGRAMS string literal out of the firmware source."""
    text = FW_PATH.read_text()
    m = re.search(r'DIGRAMS\[\d+\]\s*=\s*"([^"]*)"', text)
    if not m:
        raise ValueError("DIGRAMS table not found in firmware")
    return m.group(1)


def parse_names_hash() -> int:
    text = FW_PATH.read_text()
    m = re.search(r"#define\s+NAMES_HASH\s+(0x[0-9A-Fa-f]+|\d+)", text)
    return int(m.group(1), 0) if m else None


def seed_for_system(galaxy_idx: int, idx: int):
    """The three seeds at the point galaxy_extract reads system `idx`."""
    s = galaxy.galaxy_seeds(galaxy_idx)
    for _ in range(idx + 1):
        galaxy.step(s)
    return s


def name_from_seed(s, digrams: str) -> str:
    s = list(s)  # local copy; never mutate caller
    pairs = 4 if (s[0] & 0x40) else 3  # bit 6 of s0_lo
    out = []
    for _ in range(pairs):
        idx = (s[2] >> 8) & 0x1F  # s2_hi & %11111
        if idx != 0:
            out.append(digrams[idx * 2])
            c2 = digrams[idx * 2 + 1]
            if c2 != "?":  # '?' => one-letter token
                out.append(c2)
        galaxy.step(s)  # TT54 twist
    return "".join(out)


def system_name(galaxy_idx: int, idx: int, digrams: str | None = None) -> str:
    if digrams is None:
        digrams = parse_digrams()
    return name_from_seed(seed_for_system(galaxy_idx, idx), digrams)


def names_hash(n: int = 8, digrams: str | None = None) -> int:
    """16-bit rolling hash over the first `n` system names of galaxy 0 — the
    exact value fw/main.c's names_self_test() folds into the DBG sentinel."""
    if digrams is None:
        digrams = parse_digrams()
    h = 0
    for idx in range(n):
        for ch in system_name(0, idx, digrams):
            h = (h * 31 + ord(ch)) & 0xFFFF
        h = (h * 31) & 0xFFFF  # name separator
    return h


def check() -> tuple[bool, str]:
    digrams = parse_digrams()

    if len(digrams) != 64:
        return False, f"DIGRAMS is {len(digrams)} chars, expected 64 (32 pairs)"
    if digrams != _CLASSIC:
        return False, "DIGRAMS does not match the classic Elite digram table"

    # 1. Names are 0..8 letters, all uppercase A-Z (the '?' marker never leaks).
    seen = {}
    for idx in range(galaxy.SYSTEMS_PER_GALAXY):
        nm = system_name(0, idx, digrams)
        if len(nm) > 8:
            return False, f"system {idx} name '{nm}' exceeds 8 letters"
        if any(not ("A" <= c <= "Z") for c in nm):
            return False, f"system {idx} name '{nm}' has a non-letter"
        seen[idx] = nm

    # 2. Determinism: regenerating gives the identical name.
    if any(system_name(0, i, digrams) != seen[i] for i in range(20)):
        return False, "name generation is not deterministic"

    # 3. Variety: the 256 names are not all identical (the generator actually
    #    depends on the seed) and most are non-empty.
    distinct = len(set(seen.values()))
    nonempty = sum(1 for v in seen.values() if v)
    if distinct < 50:
        return False, f"only {distinct} distinct names across 256 systems (too few)"
    if nonempty < 200:
        return False, f"only {nonempty}/256 names are non-empty"

    # 4. Cross-check the C and Python generators agree, via the firmware's
    #    locked NAMES_HASH (folded into DBG by names_self_test).
    fw_hash = parse_names_hash()
    py_hash = names_hash(8, digrams)
    if fw_hash is None:
        return False, "firmware NAMES_HASH constant not found"
    if fw_hash != py_hash:
        return False, (
            f"NAMES_HASH mismatch: firmware 0x{fw_hash:04X} vs "
            f"oracle 0x{py_hash:04X} (C/Python name drift)"
        )

    sample = ", ".join(seen[i] for i in range(6))
    return True, (
        f"classic 32-pair table; 256 names <=8 letters A-Z; "
        f"{distinct} distinct, {nonempty} non-empty; deterministic; "
        f"NAMES_HASH 0x{fw_hash:04X} matches (C==Python). e.g. {sample}"
    )


if __name__ == "__main__":
    dg = parse_digrams()
    print("first 12 galaxy-0 names:", [system_name(0, i, dg) for i in range(12)])
    print(f"NAMES_HASH = 0x{names_hash(8, dg):04X}")
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "names: " + msg)
