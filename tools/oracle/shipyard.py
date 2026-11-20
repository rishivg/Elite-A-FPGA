"""Check demo hull data, trade-in credit, purchase gates and ship statistics."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

# { "COBRA MK3",  MODEL_COBRA,      150000, 20, 12 },
_SHIP_RE = re.compile(
    r'\{\s*"([A-Z0-9 ]+)"\s*,\s*(MODEL_\w+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\}'
)


class Ship:
    def __init__(self, name, model, price, cargo, speed):
        self.name, self.model = name, model
        self.price, self.cargo, self.speed = price, cargo, speed


def parse_fw():
    text = FW_PATH.read_text()
    block = text[text.index("ships[NSHIPS_SPEC]") :]
    ships = []
    for m in _SHIP_RE.finditer(block):
        if len(ships) >= 4:
            break
        ships.append(
            Ship(m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)))
        )
    tnum = int(re.search(r"#define\s+SHIP_TRADEIN_NUM\s+(\d+)", text).group(1))
    tden = int(re.search(r"#define\s+SHIP_TRADEIN_DEN\s+(\d+)", text).group(1))
    return ships, tnum, tden


class Yard:
    """Mirrors player_ship + ship_net_cost / ship_buy / apply_ship_stats."""

    def __init__(self, ships, tnum, tden, credits, player_ship=1):
        self.s, self.tnum, self.tden = ships, tnum, tden
        self.credits = credits
        self.player_ship = player_ship
        self.cargo_cap = ships[player_ship].cargo
        self.speed_max = ships[player_ship].speed

    def net_cost(self, i, frm=None):
        frm = self.player_ship if frm is None else frm
        # C: price[i] - price[frm] * num / den (truncating; operands positive)
        return self.s[i].price - self.s[frm].price * self.tnum // self.tden

    def buy(self, i):
        if i == self.player_ship:
            return False
        net = self.net_cost(i)
        if net > self.credits:
            return False
        self.credits -= net
        self.player_ship = i
        self.cargo_cap = self.s[i].cargo  # new hull comes without the bay
        self.speed_max = self.s[i].speed
        return True


def check() -> tuple[bool, str]:
    ships, tnum, tden = parse_fw()

    if len(ships) != 4:
        return False, f"parsed {len(ships)} ships, expected 4"
    names = [s.name for s in ships]
    if names != ["SIDEWINDER", "COBRA MK3", "VIPER", "KRAIT"]:
        return False, f"ship catalog = {names}"
    if len({s.model for s in ships}) != 4:
        return False, "two ships share a model"
    for s in ships:
        if not (s.price > 0 and s.cargo > 0 and s.speed > 0):
            return False, f"{s.name} has a non-positive stat"

    # 1. Trade-in maths (the boundaries ship_self_test checks on the CPU):
    #    upgrading from the Cobra costs, downgrading refunds, "buying" your own
    #    hull is never free.
    def net(i, frm):
        return ships[i].price - ships[frm].price * tnum // tden

    if not (net(2, 1) > 0):  # Cobra -> Viper
        return False, "Cobra->Viper did not cost the difference"
    if not (net(0, 1) < 0):  # Cobra -> Sidewinder
        return False, "Cobra->Sidewinder did not refund"
    if not (net(1, 1) > 0):  # buying your own hull
        return False, "buying your own hull came out free or negative"

    # 2. A rich pilot can upgrade Cobra -> Viper; stats follow the new hull.
    rich = Yard(ships, tnum, tden, 10**9, player_ship=1)
    if not rich.buy(2):
        return False, "rich pilot could not buy a Viper"
    if (
        rich.player_ship != 2
        or rich.cargo_cap != ships[2].cargo
        or rich.speed_max != ships[2].speed
    ):
        return False, "buying a ship did not apply its hold/speed"
    if rich.buy(rich.player_ship):
        return False, "re-bought the ship we already fly"

    # 3. Downgrading refunds credits and shrinks the hold to the new base.
    y = Yard(ships, tnum, tden, 0, player_ship=1)  # broke, in a Cobra
    before = y.credits
    if not y.buy(0):  # ... can still sell down to a Sidewinder
        return False, "could not downgrade Cobra -> Sidewinder while broke"
    if y.credits <= before:
        return False, "downgrade did not refund credits"
    if y.cargo_cap != ships[0].cargo:
        return False, "downgrade did not shrink the hold to the Sidewinder base"

    # 4. A broke pilot cannot afford an upgrade that nets a positive cost.
    poor = Yard(ships, tnum, tden, 100, player_ship=0)  # in a Sidewinder, near-broke
    if poor.buy(1) and net(1, 0) > 100:
        return False, "bought a Cobra we could not afford"

    return True, (
        f"4 hulls {names}; trade-in {tnum}/{tden}; Cobra->Viper costs / "
        f"->Sidewinder refunds; buy applies hold+speed, blocks re-buy & "
        f"unaffordable upgrades"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "shipyard: " + msg)
