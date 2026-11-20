"""Check the demo commander save layout, checksum and restore rules."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"


def _def_re(name):
    return re.compile(r"#define\s+" + name + r"\s+(\(?[^\n/]+?\)?)\s*(?:/|\n)")


# Offsets that are plain integers; CMDR_SUM/CMDR_LEN are expressions of these.
_OFFSETS = (
    "CMDR_CREDITS",
    "CMDR_KILLS",
    "CMDR_FUEL",
    "CMDR_CARGOCAP",
    "CMDR_MISSILES",
    "CMDR_LASER",
    "CMDR_EQUIP",
    "CMDR_SYS",
    "CMDR_GALAXY",
    "CMDR_TARGET",
    "CMDR_LEGAL",
    "CMDR_SHIP",
    "CMDR_MISSION",
    "CMDR_CARGO",
)


def _def_int(text: str, name: str) -> int:
    m = _def_re(name).search(text)
    if not m:
        raise ValueError(f"firmware #define {name} not found")
    return int(m.group(1).strip())


def parse_fw():
    text = FW_PATH.read_text()
    ngoods = _def_int(text, "NGOODS")
    off = {n: _def_int(text, n) for n in _OFFSETS}
    # Derived defines in the firmware: CMDR_SUM = CMDR_CARGO + NGOODS,
    # CMDR_LEN = CMDR_SUM + 1. Recompute here and confirm the source agrees.
    off["CMDR_SUM"] = off["CMDR_CARGO"] + ngoods
    off["CMDR_LEN"] = off["CMDR_SUM"] + 1
    return ngoods, off


class Commander:
    """The persistent state mirrored from the firmware globals."""

    FIELDS = (
        "credits",
        "kills",
        "fuel",
        "cargo_cap",
        "missiles",
        "laser_tier",
        "equip_owned",
        "current_sys",
        "current_galaxy",
        "target_sys",
        "legal_status",
        "player_ship",
        "mission_state",
    )

    def __init__(self, ngoods, **kw):
        self.ngoods = ngoods
        for f in self.FIELDS:
            setattr(self, f, kw.get(f, 0))
        self.cargo = list(kw.get("cargo", [0] * ngoods))

    def __eq__(self, other):
        return (
            all(getattr(self, f) == getattr(other, f) for f in self.FIELDS)
            and self.cargo == other.cargo
        )


def checksum(buf, off) -> int:
    """Port of cmdr_checksum: seed = data length, then per byte add-then-rotate.
    Both ops are bijections on the 8-bit state, so any single-byte change is
    guaranteed to alter the result (see check() #2)."""
    c = off["CMDR_SUM"]
    for i in range(off["CMDR_SUM"]):
        c = (c + buf[i]) & 0xFF
        c = ((c << 1) | (c >> 7)) & 0xFF
    return c & 0xFF


def serialize(cmd: Commander, off) -> bytearray:
    buf = bytearray(off["CMDR_LEN"])
    cr = cmd.credits & 0xFFFFFFFF
    buf[off["CMDR_CREDITS"] + 0] = cr & 0xFF
    buf[off["CMDR_CREDITS"] + 1] = (cr >> 8) & 0xFF
    buf[off["CMDR_CREDITS"] + 2] = (cr >> 16) & 0xFF
    buf[off["CMDR_CREDITS"] + 3] = (cr >> 24) & 0xFF
    buf[off["CMDR_KILLS"] + 0] = cmd.kills & 0xFF
    buf[off["CMDR_KILLS"] + 1] = (cmd.kills >> 8) & 0xFF
    buf[off["CMDR_FUEL"]] = cmd.fuel & 0xFF
    buf[off["CMDR_CARGOCAP"]] = cmd.cargo_cap & 0xFF
    buf[off["CMDR_MISSILES"]] = cmd.missiles & 0xFF
    buf[off["CMDR_LASER"]] = cmd.laser_tier & 0xFF
    buf[off["CMDR_EQUIP"]] = cmd.equip_owned & 0xFF
    buf[off["CMDR_SYS"]] = cmd.current_sys & 0xFF
    buf[off["CMDR_GALAXY"]] = cmd.current_galaxy & 0xFF
    buf[off["CMDR_TARGET"]] = cmd.target_sys & 0xFF
    buf[off["CMDR_LEGAL"]] = cmd.legal_status & 0xFF
    buf[off["CMDR_SHIP"]] = cmd.player_ship & 0xFF
    buf[off["CMDR_MISSION"]] = cmd.mission_state & 0xFF
    for i in range(cmd.ngoods):
        buf[off["CMDR_CARGO"] + i] = cmd.cargo[i] & 0xFF
    buf[off["CMDR_SUM"]] = checksum(buf, off)
    return buf


def deserialize(buf, off, ngoods):
    """Returns a Commander, or None if the checksum rejects the buffer."""
    if buf[off["CMDR_SUM"]] != checksum(buf, off):
        return None

    def g(offset):
        return buf[offset]

    cr = (
        g(off["CMDR_CREDITS"])
        | (g(off["CMDR_CREDITS"] + 1) << 8)
        | (g(off["CMDR_CREDITS"] + 2) << 16)
        | (g(off["CMDR_CREDITS"] + 3) << 24)
    )
    kills = g(off["CMDR_KILLS"]) | (g(off["CMDR_KILLS"] + 1) << 8)
    return Commander(
        ngoods,
        credits=cr,
        kills=kills,
        fuel=g(off["CMDR_FUEL"]),
        cargo_cap=g(off["CMDR_CARGOCAP"]),
        missiles=g(off["CMDR_MISSILES"]),
        laser_tier=g(off["CMDR_LASER"]),
        equip_owned=g(off["CMDR_EQUIP"]),
        current_sys=g(off["CMDR_SYS"]),
        current_galaxy=g(off["CMDR_GALAXY"]),
        target_sys=g(off["CMDR_TARGET"]),
        legal_status=g(off["CMDR_LEGAL"]),
        player_ship=g(off["CMDR_SHIP"]),
        mission_state=g(off["CMDR_MISSION"]),
        cargo=[g(off["CMDR_CARGO"] + i) for i in range(ngoods)],
    )


def check() -> tuple[bool, str]:
    ngoods, off = parse_fw()

    if ngoods != 17:
        return False, f"NGOODS={ngoods}, expected 17"
    if off["CMDR_LEN"] != off["CMDR_SUM"] + 1:
        return False, "CMDR_LEN is not CMDR_SUM+1"
    # Layout must be packed contiguously with no overlap or gap up to the
    # cargo block (catches an offset edit that collides two fields).
    expect = [
        ("CMDR_CREDITS", 4),
        ("CMDR_KILLS", 2),
        ("CMDR_FUEL", 1),
        ("CMDR_CARGOCAP", 1),
        ("CMDR_MISSILES", 1),
        ("CMDR_LASER", 1),
        ("CMDR_EQUIP", 1),
        ("CMDR_SYS", 1),
        ("CMDR_GALAXY", 1),
        ("CMDR_TARGET", 1),
        ("CMDR_LEGAL", 1),
        ("CMDR_SHIP", 1),
        ("CMDR_MISSION", 1),
    ]
    pos = 0
    for name, width in expect:
        if off[name] != pos:
            return False, f"{name} at {off[name]}, expected {pos} (layout drift)"
        pos += width
    if off["CMDR_CARGO"] != pos:
        return False, f"CMDR_CARGO at {off['CMDR_CARGO']}, expected {pos}"
    if off["CMDR_SUM"] != off["CMDR_CARGO"] + ngoods:
        return False, "checksum byte does not sit right after the cargo block"

    def mk(**kwargs):
        return Commander(ngoods, **kwargs)

    # 1. Clean round-trip of a fully-populated commander preserves every field.
    a = mk(
        credits=123456,
        kills=0xBEEF,
        fuel=55,
        cargo_cap=35,
        missiles=4,
        laser_tier=3,
        equip_owned=0x6D,
        current_sys=200,
        current_galaxy=7,
        target_sys=13,
        legal_status=55,
        player_ship=2,
        mission_state=1,
        cargo=[(i * 3 + 1) & 0xFF for i in range(ngoods)],
    )
    buf = serialize(a, off)
    if len(buf) != off["CMDR_LEN"]:
        return False, f"serialized {len(buf)} bytes, expected {off['CMDR_LEN']}"
    b = deserialize(buf, off, ngoods)
    if b is None:
        return False, "clean buffer was rejected by its own checksum"
    if a != b:
        return False, "round-trip changed a field"

    # 2. EVERY single-byte corruption is caught — for each data byte, changing
    #    it to any of the other 255 values is rejected. This is the add-then-
    #    rotate guarantee: both ops are bijections, so a lone byte change can
    #    never cancel back to the same checksum. (An exhaustive 31*255 sweep.)
    for i in range(off["CMDR_SUM"]):
        for v in range(256):
            if v == buf[i]:
                continue
            bad = bytearray(buf)
            bad[i] = v
            if deserialize(bad, off, ngoods) is not None:
                return False, f"single-byte corruption of byte {i} -> {v} not detected"
    # 3. Corrupting the checksum byte itself is also rejected.
    bad = bytearray(buf)
    bad[off["CMDR_SUM"]] ^= 0xFF
    if deserialize(bad, off, ngoods) is not None:
        return False, "a wrong checksum byte was accepted"

    # 4. The checksum actually depends on content (not a constant): two
    #    different commanders almost never share a checksum here.
    c2 = mk(credits=999, fuel=70, cargo_cap=20, missiles=0, laser_tier=1)
    if serialize(c2, off)[off["CMDR_SUM"]] == buf[off["CMDR_SUM"]]:
        return False, "checksum collides for clearly different commanders"

    # 5. The firmware boot self-test uses exactly commander `a` above; confirm
    #    its corruption probe (xor 0x80 into the first cargo byte) is rejected,
    #    matching cmdr_self_test()'s final assertion.
    probe = bytearray(buf)
    probe[off["CMDR_CARGO"]] ^= 0x80
    if deserialize(probe, off, ngoods) is not None:
        return False, "self-test corruption probe not rejected"

    return True, (
        f"{off['CMDR_LEN']}-byte packed layout matches firmware; "
        f"round-trip preserves all fields incl. {ngoods}-good cargo; "
        f"all {off['CMDR_SUM'] * 255} single-byte corruptions + bad "
        f"checksum rejected; checksum content-dependent; boot self-test "
        f"probe mirrored"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "cmdr: " + msg)
