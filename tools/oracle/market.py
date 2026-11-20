"""Model demo commodity prices, cargo capacity and buy/sell transactions."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

_GOOD_RE = re.compile(
    r'\{\s*"([A-Z]{3})"\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(0x[0-9A-Fa-f]+)\s*\}'
)
_CARGO_RE = re.compile(r"#define\s+CARGO_MAX\s+(\d+)")
_CREDIT_RE = re.compile(r"static\s+long\s+credits\s*=\s*(\d+)")
_RATING_RE = re.compile(r"RATING_KILLS\s*\[\s*9\s*\]\s*=\s*\{([^}]*)\}")


def parse_fw():
    text = FW_PATH.read_text()
    # Only the goods[] initialiser, not the typedef field comments.
    block = text[text.index("goods[NGOODS]") :]
    goods = []
    for m in _GOOD_RE.finditer(block):
        if len(goods) >= 17:
            break
        goods.append(
            {
                "abbr": m.group(1),
                "base": int(m.group(2)),
                "grad": int(m.group(3)),
                "bqty": int(m.group(4)),
                "mask": int(m.group(5), 0),
            }
        )
    cargo_max = int(_CARGO_RE.search(text).group(1))
    credits0 = int(_CREDIT_RE.search(text).group(1))
    rating_kills = [int(s.strip(), 0) for s in _RATING_RE.search(text).group(1).split(",")]
    return goods, cargo_max, credits0, rating_kills


def market_gen(goods, econ, fluct):
    """Return (price_tenths[], quantity[]) for an economy index + fluctuation byte."""
    prices, qtys = [], []
    for g in goods:
        pu = (g["base"] + econ * g["grad"] + (fluct & g["mask"])) & 0xFF
        prices.append(pu * 4)
        q = g["bqty"] - econ * g["grad"] + (fluct & g["mask"])
        q = max(0, min(63, q))
        qtys.append(q)
    return prices, qtys


def commander_rating(rating_kills, kills):
    r = 0
    for i, thr in enumerate(rating_kills):
        if kills >= thr:
            r = i
    return r


class Trader:
    """Mirrors the firmware's buy/sell state machine."""

    def __init__(self, goods, cargo_max, credits, prices, qtys):
        self.goods = goods
        self.cargo_max = cargo_max
        self.credits = credits
        self.prices = list(prices)
        self.qtys = list(qtys)
        self.cargo = [0] * len(goods)

    def used(self):
        return sum(self.cargo)

    def buy(self, i):
        if self.qtys[i] == 0:
            return False
        if self.used() >= self.cargo_max:
            return False
        if self.credits < self.prices[i]:
            return False
        self.credits -= self.prices[i]
        self.cargo[i] += 1
        self.qtys[i] -= 1
        return True

    def sell(self, i):
        if self.cargo[i] == 0:
            return False
        self.credits += self.prices[i]
        self.cargo[i] -= 1
        self.qtys[i] += 1
        return True


def check() -> tuple[bool, str]:
    goods, cargo_max, credits0, rating_kills = parse_fw()

    # 1. Table shape.
    if len(goods) != 17:
        return False, f"parsed {len(goods)} goods, expected 17"
    if rating_kills != [0, 2, 4, 8, 16, 32, 64, 128, 256]:
        return False, f"RATING_KILLS ladder = {rating_kills}"

    # 2. Price/quantity formula bounds for the whole economy range.
    for econ in range(8):
        for fluct in (0, 73, 200, 255):
            prices, qtys = market_gen(goods, econ, fluct)
            for i, (p, q) in enumerate(zip(prices, qtys)):
                if not (0 <= p <= 255 * 4):
                    return False, f"price[{i}] {p} out of byte*4 range (econ={econ},fluct={fluct})"
                if p % 4 != 0:
                    return False, f"price[{i}] {p} not a multiple of 4"
                if not (0 <= q <= 63):
                    return False, f"qty[{i}] {q} out of [0,63] (econ={econ},fluct={fluct})"

    # 3. Hand-checked vector: Food (base 19, grad -2, mask 0x01) at econ=3,
    #    fluct=0xFF -> units = (19 - 6 + 1) & 0xFF = 14 -> price 56 tenths;
    #    qty = clamp(12 + 6 + 1) = 19.
    p, q = market_gen(goods, 3, 0xFF)
    if p[0] != 56 or q[0] != 19:
        return False, f"FOO@(econ=3,fluct=0xFF) price={p[0]} qty={q[0]}, expected 56/19"

    # 4. Negative-gradient good gets cheaper as the economy index rises (model
    #    sanity), holding fluctuation fixed.
    cheap_lo, _ = market_gen(goods, 0, 0)
    cheap_hi, _ = market_gen(goods, 7, 0)
    if not (cheap_hi[0] < cheap_lo[0]):
        return False, "food price did not fall with rising economy index"

    # 5. Buy/sell round-trip + gating.
    prices, qtys = market_gen(goods, 2, 100)
    t = Trader(goods, cargo_max, credits0, prices, qtys)
    c0, q0 = t.credits, t.qtys[0]
    if not t.buy(0):
        return False, "first buy rejected unexpectedly"
    if t.credits != c0 - prices[0] or t.cargo[0] != 1 or t.qtys[0] != q0 - 1:
        return False, "buy did not move credits/cargo/qty correctly"
    if not t.sell(0):
        return False, "sell rejected unexpectedly"
    if t.credits != c0 or t.cargo[0] != 0 or t.qtys[0] != q0:
        return False, "sell did not invert buy"
    if t.sell(0):
        return False, "sell with empty hold should fail"

    # 6. Hold-capacity gate: buying a cheap, plentiful good can't exceed CARGO_MAX.
    rich = Trader(goods, cargo_max, 10**9, [4] * 17, [99] * 17)
    bought = 0
    while rich.buy(0):
        bought += 1
        if bought > cargo_max + 5:
            break
    if rich.used() != cargo_max or bought != cargo_max:
        return False, f"cargo cap breached: used={rich.used()} bought={bought} cap={cargo_max}"

    # 7. Broke gate: can't buy what you can't afford.
    broke = Trader(goods, cargo_max, 0, prices, qtys)
    if broke.buy(0):
        return False, "buy succeeded with zero credits"

    # 8. Rating ladder: monotonic, ends at ELITE (index 8) for 256 kills.
    last = -1
    for k in range(0, 300):
        r = commander_rating(rating_kills, k)
        if r < last:
            return False, f"rating regressed at {k} kills"
        last = r
    if commander_rating(rating_kills, 0) != 0:
        return False, "0 kills not HARMLESS"
    if commander_rating(rating_kills, 256) != 8:
        return False, "256 kills not ELITE"

    return True, (
        f"17 goods parsed; price=units*4 & qty in [0,63] over all econ/fluct; "
        f"FOO vector 56/19; buy/sell round-trips + broke/full gates; "
        f"cargo cap {cargo_max}; rating ladder monotone, ELITE@256"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "market: " + msg)
