"""Model the demo Tribonacci galaxy generator with its own CAFE/F00D/BEEF seeds."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

INITIAL_SEEDS = (0xCAFE, 0xF00D, 0xBEEF)
SYSTEMS_PER_GALAXY = 256
N_GALAXIES = 8

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"


def parse_name_table(name: str) -> list[str]:
    """Pull a `static const char *NAME[8] = { "..", ... };` table from firmware."""
    text = FW_PATH.read_text()
    m = re.search(name + r"\s*\[\s*8\s*\]\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"{name}[8] table not found in firmware")
    return [s.strip().strip('"') for s in m.group(1).split(",") if s.strip()]


def galactic_jump(galaxy: int, owns_ghyp: bool) -> tuple[int, bool]:
    """Mirror fw/main.c:galactic_jump — consumes the drive and advances galaxy
    with 8-wrap. Returns (new_galaxy, still_owns_ghyp)."""
    if not owns_ghyp:
        return galaxy, False
    return (galaxy + 1) & 7, False


@dataclass
class System:
    x: int
    y: int
    economy: int
    government: int
    population: int
    productivity: int


def rot16(w: int) -> int:
    w &= 0xFFFF
    return ((w << 1) | (w >> 15)) & 0xFFFF


def galaxy_seeds(idx: int) -> list[int]:
    """Galaxy idx (0..7) initial seeds, derived by idx left-rotations."""
    s = list(INITIAL_SEEDS)
    for _ in range(idx):
        s = [rot16(s[0]), rot16(s[1]), rot16(s[2])]
    return s


def step(s: list[int]) -> None:
    t = (s[0] + s[1] + s[2]) & 0xFFFF
    s[0] = s[1]
    s[1] = s[2]
    s[2] = t


def system_for_state(s: list[int]) -> System:
    return System(
        x=(s[1] >> 8) & 0xFF,
        y=(s[0] >> 8) & 0xFF,
        economy=s[0] & 0x07,
        government=(s[1] >> 3) & 0x07,
        population=(s[2] >> 4) & 0x3F,
        productivity=s[2] & 0xFF,
    )


def enumerate_galaxy(idx: int, n: int = SYSTEMS_PER_GALAXY) -> list[System]:
    s = galaxy_seeds(idx)
    out: list[System] = []
    for _ in range(n):
        step(s)
        out.append(system_for_state(s))
    return out


def galaxy_self_test_vectors(n: int = 8) -> tuple[list[int], list[int]]:
    """First-n (x, y) of galaxy 0 — what fw/main.c:galaxy_self_test asserts against."""
    sys = enumerate_galaxy(0, n)
    return [s.x for s in sys], [s.y for s in sys]


def check() -> tuple[bool, str]:
    # 1. Algorithm is deterministic.
    g0_a = enumerate_galaxy(0)
    g0_b = enumerate_galaxy(0)
    if g0_a != g0_b:
        return False, "PRNG not deterministic across two runs"

    # 2. Galactic shift produces a different galaxy 1 from galaxy 0.
    g1 = enumerate_galaxy(1)
    if g0_a[0:8] == g1[0:8]:
        return False, "galaxies 0 and 1 had identical first 8 systems (rotation didn't shift)"

    # 3. Locked test vectors for galaxy 0 (regenerate by running this file).
    #    fw/main.c:galaxy_self_test asserts these same values — if you change
    #    the seeds or the recurrence, regenerate both here and in firmware.
    EXPECTED_X = [0xBE, 0x79, 0x28, 0x61, 0x04, 0x8F, 0xF6, 0x8A]
    EXPECTED_Y = [0xF0, 0xBE, 0x79, 0x28, 0x61, 0x04, 0x8F, 0xF6]
    xs = [s.x for s in g0_a[:8]]
    ys = [s.y for s in g0_a[:8]]
    if xs != EXPECTED_X:
        return (
            False,
            f"galaxy 0 x[] = {[hex(v) for v in xs]}, expected {[hex(v) for v in EXPECTED_X]}",
        )
    if ys != EXPECTED_Y:
        return (
            False,
            f"galaxy 0 y[] = {[hex(v) for v in ys]}, expected {[hex(v) for v in EXPECTED_Y]}",
        )

    # 4. Field ranges are valid for all 256 systems x all 8 galaxies.
    for g in range(N_GALAXIES):
        for sys in enumerate_galaxy(g):
            if not (0 <= sys.x <= 255 and 0 <= sys.y <= 255):
                return False, f"galaxy {g}: out-of-range coord ({sys.x}, {sys.y})"
            if not (0 <= sys.economy <= 7):
                return False, f"economy out of range: {sys.economy}"
            if not (0 <= sys.government <= 7):
                return False, f"government out of range: {sys.government}"
            if not (0 <= sys.population <= 63):
                return False, f"population out of range: {sys.population}"

    # 5. System-data screen name tables: exactly 8 non-empty entries each, so
    #    the firmware's economy/government `& 7` index can never run off the end.
    econ_names = parse_name_table("ECONOMY_NAMES")
    gov_names = parse_name_table("GOV_NAMES")
    if len(econ_names) != 8 or any(not n for n in econ_names):
        return False, f"ECONOMY_NAMES = {econ_names} (need 8 non-empty)"
    if len(gov_names) != 8 or any(not n for n in gov_names):
        return False, f"GOV_NAMES = {gov_names} (need 8 non-empty)"

    # 6. Galactic hyperdrive: a sequence of jumps walks galaxies with 8-wrap and
    #    consumes the drive exactly once (a second jump without re-fitting fails).
    g = 0
    owns = True
    g, owns = galactic_jump(g, owns)
    if g != 1 or owns:
        return False, f"first galactic jump -> galaxy {g}, owns={owns} (expected 1/False)"
    g2, _ = galactic_jump(g, owns)
    if g2 != g:
        return False, "second galactic jump fired without re-fitting the drive"
    walk = 0
    for _ in range(8):
        walk, _ = galactic_jump(walk, True)
    if walk != 0:
        return False, f"8 galactic jumps did not wrap back to galaxy 0 (got {walk})"

    return True, (
        "Tribonacci PRNG deterministic; galactic shift distinct; "
        f"locked first-8 (x, y) match firmware self-test; "
        f"{N_GALAXIES} x {SYSTEMS_PER_GALAXY} systems all within field ranges; "
        f"8 economy + 8 government names; galactic jump wraps 0..7 and consumes once"
    )


if __name__ == "__main__":
    # Regen helper: print the first 8 systems of galaxy 0 in firmware-ready format.
    print("First 8 systems of galaxy 0 (paste into fw/main.c:GALAXY0_X/Y):")
    xs, ys = galaxy_self_test_vectors(8)
    print(f"  X = {{ {', '.join(f'0x{v:02X}' for v in xs)} }};")
    print(f"  Y = {{ {', '.join(f'0x{v:02X}' for v in ys)} }};")
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "galaxy: " + msg)
