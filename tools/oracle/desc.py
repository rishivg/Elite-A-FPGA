"""Model demo system descriptions from the firmware digram and word tables."""

from __future__ import annotations

import re
from pathlib import Path

from . import names

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

_TABLES = (
    "D_ADJ",
    "D_INTENS",
    "D_ANCIENT",
    "D_FOREST",
    "D_AFFLICT",
    "D_FREQ",
    "D_KILLER",
    "D_GOODADJ",
    "D_GOODN",
    "D_INHAB",
    "D_CUSTOM",
    "D_OBJECT",
    "D_DULL",
    "D_PLACE",
)


def parse_tables() -> dict:
    text = FW_PATH.read_text()
    out = {}
    for name in _TABLES:
        m = re.search(name + r"\[5\][^=]*=\s*\{([^}]*)\}", text)
        if not m:
            raise ValueError(f"vocab table {name} not found in firmware")
        out[name] = re.findall(r'"([^"]*)"', m.group(1))
        if len(out[name]) != 5:
            raise ValueError(f"{name} has {len(out[name])} entries, expected 5")
    return out


def parse_desc_hash() -> int:
    text = FW_PATH.read_text()
    m = re.search(r"#define\s+DESC_HASH\s+(0x[0-9A-Fa-f]+|\d+)", text)
    return int(m.group(1), 0) if m else None


class Goat:
    """One description build — holds the RNG state and the output buffer, so the
    call order matches the C exactly."""

    def __init__(self, tbl, seed):
        self.t = tbl
        self.rng = seed & 0xFFFFFFFF
        self.out = []

    def rand(self, n):
        self.rng = (self.rng * 1103515245 + 12345) & 0xFFFFFFFF
        return (self.rng >> 16) % n

    def s(self, text):
        self.out.append(text)

    def pick(self, name):
        self.s(self.t[name][self.rand(5)])

    def thing(self):
        k = self.rand(3)
        if k == 0:
            self.s("MOUNTAINS")
        elif k == 1:
            self.pick("D_FOREST")
            self.s(" FORESTS")
        else:
            self.s("OCEANS")

    def custom(self):
        k = self.rand(5)
        if k == 3:
            self.s("LOATHING OF ")
            self.pick("D_OBJECT")
        elif k == 4:
            self.s("LOVE FOR ")
            self.pick("D_OBJECT")
        else:
            self.s(self.t["D_CUSTOM"][k])

    def feature(self):
        k = self.rand(3)
        if k == 0:
            self.s("ITS ")
            self.pick("D_ANCIENT")
            self.s(" ")
            self.thing()
        elif k == 1:
            self.s("ITS INHABITANTS' ")
            self.pick("D_INHAB")
            self.s(" ")
            self.custom()
        else:
            self.s("ITS ")
            self.pick("D_GOODADJ")
            self.s(" ")
            self.pick("D_GOODN")

    def calamity(self):
        k = self.rand(5)
        if k == 0:
            self.pick("D_FREQ")
            self.s(" CIVIL WAR")
        elif k == 1:
            self.pick("D_KILLER")
            self.s(" CREATURES")
        elif k == 2:
            self.s("A ")
            self.pick("D_KILLER")
            self.s(" DISEASE")
        elif k == 3:
            self.pick("D_FREQ")
            self.s(" EARTHQUAKES")
        else:
            self.pick("D_FREQ")
            self.s(" SOLAR ACTIVITY")

    def intens_adj_for(self):
        self.pick("D_INTENS")
        self.pick("D_ADJ")
        self.s(" FOR ")

    def predicate(self):
        k = self.rand(5)
        if k == 0:
            self.intens_adj_for()
            self.feature()
        elif k == 1:
            self.intens_adj_for()
            self.feature()
            self.s(" AND ")
            self.feature()
        elif k == 2:
            self.pick("D_AFFLICT")
            self.s(" BY ")
            self.calamity()
        elif k == 3:
            self.intens_adj_for()
            self.feature()
            self.s(" BUT ")
            self.pick("D_AFFLICT")
            self.s(" BY ")
            self.calamity()
        else:
            self.s("A")
            self.pick("D_DULL")
            self.s(" ")
            self.pick("D_PLACE")


def describe_system(galaxy_idx: int, idx: int, tbl=None, digrams=None) -> str:
    if tbl is None:
        tbl = parse_tables()
    s = names.seed_for_system(galaxy_idx, idx)
    name = names.name_from_seed(s, digrams or names.parse_digrams())
    g = Goat(tbl, ((s[1] << 16) | s[2]))
    k = g.rand(5)  # subject (Elite tokens 86-90)
    if k == 1:
        g.s("THE PLANET ")
        g.s(name or "?")
    elif k == 2:
        g.s("THE WORLD ")
        g.s(name or "?")
    elif k == 3:
        g.s("THIS PLANET")
    elif k == 4:
        g.s("THIS WORLD")
    else:
        g.s(name or "?")
    g.s(" IS ")
    g.predicate()
    g.s(".")
    return "".join(g.out)


def desc_hash(n: int = 8, tbl=None, digrams=None) -> int:
    if tbl is None:
        tbl = parse_tables()
    if digrams is None:
        digrams = names.parse_digrams()
    h = 0
    for idx in range(n):
        for ch in describe_system(0, idx, tbl, digrams):
            h = (h * 31 + ord(ch)) & 0xFFFF
        h = (h * 31) & 0xFFFF
    return h


def check() -> tuple[bool, str]:
    tbl = parse_tables()
    digrams = names.parse_digrams()

    descs = [describe_system(0, i, tbl, digrams) for i in range(256)]

    # 1. Every description is a well-formed sentence: non-empty, "<x> IS <y>.".
    for i, d in enumerate(descs):
        if not d.endswith("."):
            return False, f"system {i} description does not end with '.': {d!r}"
        if " IS " not in d:
            return False, f"system {i} description has no ' IS ': {d!r}"
        if len(d) > 199:
            return False, f"system {i} description overflows the buffer ({len(d)})"

    # 2. Determinism.
    if any(describe_system(0, i, tbl, digrams) != descs[i] for i in range(20)):
        return False, "description generation is not deterministic"

    # 3. Variety: the grammar actually branches.
    distinct = len(set(descs))
    if distinct < 100:
        return False, f"only {distinct} distinct descriptions across 256 systems"

    # 4. C/Python parity via the firmware's locked DESC_HASH.
    fw_hash = parse_desc_hash()
    py_hash = desc_hash(8, tbl, digrams)
    if fw_hash is None:
        return False, "firmware DESC_HASH constant not found"
    if fw_hash != py_hash:
        return False, (
            f"DESC_HASH mismatch: firmware 0x{fw_hash:04X} vs oracle "
            f"0x{py_hash:04X} (C/Python grammar drift)"
        )

    return True, (
        f"goat-soup grammar mirrors firmware; 256 sentences well-formed "
        f"(<x> IS <y>.), {distinct} distinct, deterministic; "
        f"DESC_HASH 0x{fw_hash:04X} matches (C==Python)"
    )


if __name__ == "__main__":
    tbl = parse_tables()
    dg = names.parse_digrams()
    for i in range(8):
        print(f"  {i}: {describe_system(0, i, tbl, dg)}")
    print(f"DESC_HASH = 0x{desc_hash(8, tbl, dg):04X}")
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "desc: " + msg)
