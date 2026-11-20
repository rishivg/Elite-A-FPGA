"""Model demo equipment prices, purchase gates and resulting ship state."""

from __future__ import annotations

import re
from pathlib import Path

from . import market

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

# { "FUEL",    140, EK_FUEL,    0 },
_EQ_RE = re.compile(r'\{\s*"([A-Z]+)"\s*,\s*(\d+)\s*,\s*(EK_\w+)\s*,\s*(\w+)\s*\}')


def _def_re(name):
    return re.compile(r"#define\s+" + name + r"\s+(\d+)")


def parse_fw():
    text = FW_PATH.read_text()
    block = text[text.index("equip[NEQUIP]") :]
    items = []
    for m in _EQ_RE.finditer(block):
        if len(items) >= 13:
            break
        items.append(
            {
                "name": m.group(1),
                "price": int(m.group(2)),
                "kind": m.group(3),
                "arg": m.group(4),  # tier int (as str) for EK_LASER, EQ_* for EK_FLAG
            }
        )
    consts = {
        n: int(_def_re(n).search(text).group(1))
        for n in ("FUEL_MAX", "MISSILES_MAX", "LARGE_CARGO_CAP", "CARGO_MAX")
    }
    return items, consts


class Ship:
    """Mirrors the firmware's equipment state + equip_buy / equip_available."""

    def __init__(self, items, consts, credits):
        self.items = items
        self.c = consts
        self.credits = credits
        self.fuel = consts["FUEL_MAX"]
        self.missiles = 0
        self.cargo_cap = consts["CARGO_MAX"]
        self.laser_tier = 1
        self.owned = set()
        self.missile_active = False
        self.cargo_t = 0  # for escape-pod cargo-loss check

    def available(self, i):
        e = self.items[i]
        k = e["kind"]
        if k == "EK_FUEL":
            return self.fuel < self.c["FUEL_MAX"]
        if k == "EK_MISSILE":
            return self.missiles < self.c["MISSILES_MAX"]
        if k == "EK_CARGO":
            return self.cargo_cap < self.c["LARGE_CARGO_CAP"]
        if k == "EK_LASER":
            return self.laser_tier < int(e["arg"])
        if k == "EK_FLAG":
            return e["arg"] not in self.owned
        return False

    def buy(self, i):
        e = self.items[i]
        if not self.available(i):
            return False
        if self.credits < e["price"]:
            return False
        self.credits -= e["price"]
        k = e["kind"]
        if k == "EK_FUEL":
            self.fuel = self.c["FUEL_MAX"]
        elif k == "EK_MISSILE":
            self.missiles += 1
        elif k == "EK_CARGO":
            self.cargo_cap = self.c["LARGE_CARGO_CAP"]
        elif k == "EK_LASER":
            self.laser_tier = int(e["arg"])
        elif k == "EK_FLAG":
            self.owned.add(e["arg"])
        return True

    def laser_damage(self):
        return self.laser_tier

    # --- combat activation (mirrors fw missile_launch / energy_bomb / ECM / pod) ---
    def missile_launch(self, has_target):
        if self.missile_active or self.missiles <= 0 or not has_target:
            return False
        self.missiles -= 1
        self.missile_active = True
        return True

    def energy_bomb(self):
        if "EQ_ENERGYBOMB" not in self.owned:
            return False
        self.owned.discard("EQ_ENERGYBOMB")  # consumed
        return True

    def enemy_hit(self, shields):
        """Return (new_shields, jammed). ECM jams the shot entirely."""
        if "EQ_ECM" in self.owned:
            return shields, True
        return max(0, shields - 1), False

    def lethal_hit(self):
        """Called when shields would hit 0. Escape pod -> bail (consume + lose
        cargo + survive); returns True if the pod saved us, else False = death."""
        if "EQ_ESCAPEPOD" in self.owned:
            self.owned.discard("EQ_ESCAPEPOD")
            self.cargo_t = 0
            return True
        return False


def check() -> tuple[bool, str]:
    items, consts = parse_fw()
    _, _, credits0, _ = market.parse_fw()

    if len(items) != 13:
        return False, f"parsed {len(items)} equipment items, expected 13"

    kinds = {e["kind"] for e in items}
    if not {"EK_FUEL", "EK_MISSILE", "EK_CARGO", "EK_LASER", "EK_FLAG"} <= kinds:
        return False, f"missing equipment kinds; saw {sorted(kinds)}"

    idx = {e["name"]: i for i, e in enumerate(items)}

    # 1. Rich ship: each one-shot FLAG/CARGO item buys once, then is rejected.
    #    (Lasers are tier-ordered, not independent one-shots — tested in #3;
    #    fuel/missiles are capacity-bounded — tested in #4/#5.)
    rich = Ship(items, consts, 10**9)
    for i, e in enumerate(items):
        if e["kind"] not in ("EK_CARGO", "EK_FLAG"):
            continue
        if not rich.buy(i):
            return False, f"{e['name']}: first purchase rejected on a rich ship"
        if rich.buy(i):
            return False, f"{e['name']}: bought twice (one-shot not gated)"

    # 2. After CARGO upgrade the cargo cap is LARGE_CARGO_CAP, and the market
    #    hold integration follows (a fresh market can be filled to the new cap).
    if rich.cargo_cap != consts["LARGE_CARGO_CAP"]:
        return False, f"cargo cap after upgrade = {rich.cargo_cap}"
    goods, _, _, _ = market.parse_fw()
    t = market.Trader(goods, rich.cargo_cap, 10**9, [4] * 17, [99] * 17)
    n = 0
    while t.buy(0):
        n += 1
        if n > rich.cargo_cap + 5:
            break
    if t.used() != consts["LARGE_CARGO_CAP"]:
        return False, f"large hold filled to {t.used()}, expected {consts['LARGE_CARGO_CAP']}"

    # 3. Laser tier => combat damage; tiers only ever increase.
    if rich.laser_damage() != 1:
        return False, f"fresh laser damage = {rich.laser_damage()}, expected 1 (pulse)"
    if not rich.buy(idx["BEAM"]) or rich.laser_damage() != 2:
        return False, f"beam laser did not raise damage to 2 (got {rich.laser_damage()})"
    if not rich.buy(idx["MILSR"]) or rich.laser_damage() != 3:
        return False, f"military laser did not raise damage to 3 (got {rich.laser_damage()})"
    # Buying a lower-tier laser when you already have a better one is rejected.
    if rich.buy(idx["BEAM"]) or rich.buy(idx["MINSR"]):
        return False, "lower-tier laser purchasable after MILSR (tier downgrade not gated)"

    # 4. Fuel: a full tank can't be refuelled; once drained, one purchase tops up.
    s = Ship(items, consts, credits0)
    if s.buy(idx["FUEL"]):
        return False, "refuel succeeded on a full tank"
    s.fuel = 0
    c_before = s.credits
    if not s.buy(idx["FUEL"]):
        return False, "refuel rejected on an empty tank"
    if s.fuel != consts["FUEL_MAX"] or s.credits != c_before - items[idx["FUEL"]]["price"]:
        return False, "refuel did not top up / charge correctly"

    # 5. Missiles accumulate to MISSILES_MAX then stop.
    s2 = Ship(items, consts, 10**9)
    bought = 0
    while s2.buy(idx["MISSL"]):
        bought += 1
        if bought > consts["MISSILES_MAX"] + 3:
            break
    if s2.missiles != consts["MISSILES_MAX"] or bought != consts["MISSILES_MAX"]:
        return False, f"missiles capped wrong: have {s2.missiles} bought {bought}"

    # 6. Broke ship can't buy anything.
    broke = Ship(items, consts, 0)
    if any(broke.buy(i) for i in range(13)):
        return False, "a purchase succeeded with zero credits"

    # 7. Docking computer flag is what the firmware gates DOCKIT on.
    dc = Ship(items, consts, 10**9)
    dc.buy(idx["DOCK"])
    if "EQ_DOCKCOMP" not in dc.owned:
        return False, "DOCK purchase did not set EQ_DOCKCOMP"

    # 8. Missile launch: needs a missile AND a target; decrements; no double-fire.
    m = Ship(items, consts, 10**9)
    if m.missile_launch(True):
        return False, "launched a missile with none aboard"
    m.buy(idx["MISSL"])
    m.buy(idx["MISSL"])  # two missiles
    if m.missile_launch(False):
        return False, "launched a missile with no target lock"
    if not m.missile_launch(True) or m.missiles != 1:
        return False, f"launch did not fire/decrement (missiles={m.missiles})"
    if m.missile_launch(True):
        return False, "second missile fired while one is already in flight"

    # 9. ECM jams incoming fire; without it shields drop by one.
    plain = Ship(items, consts, 10**9)
    s_after, jammed = plain.enemy_hit(5)
    if jammed or s_after != 4:
        return False, f"no-ECM hit: shields {s_after}, jammed={jammed} (expected 4/False)"
    ecm = Ship(items, consts, 10**9)
    ecm.buy(idx["ECM"])
    s_after, jammed = ecm.enemy_hit(5)
    if not jammed or s_after != 5:
        return False, f"ECM hit: shields {s_after}, jammed={jammed} (expected 5/True)"

    # 10. Energy bomb fires once then is consumed; escape pod saves once.
    eb = Ship(items, consts, 10**9)
    eb.buy(idx["EBOMB"])
    if not eb.energy_bomb() or eb.energy_bomb():
        return False, "energy bomb did not fire-once-then-consume"
    ep = Ship(items, consts, 10**9)
    ep.buy(idx["EPOD"])
    ep.cargo_t = 5
    if not ep.lethal_hit() or ep.cargo_t != 0 or ep.lethal_hit():
        return False, "escape pod did not save-once + dump cargo"

    return True, (
        f"13 items parsed; one-shots gate-once; CARGO -> hold {consts['LARGE_CARGO_CAP']} "
        f"(market integration); laser tier->damage 3, no downgrade; refuel full/empty "
        f"gates; missiles cap {consts['MISSILES_MAX']}; broke gate; DOCK sets EQ_DOCKCOMP; "
        f"missile launch gates+decrements; ECM jams; bomb/pod fire-once"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "equip: " + msg)
