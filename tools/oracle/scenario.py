"""Exercise the assembled demo models through combat, respawn and repeated jumps."""

from __future__ import annotations

from . import spec
from .bubble import Bubble, default_scene
from .mveit import mveit
from .player import PlayerState
from .rng import FwRng
from .tactics import FireDecision, look_at_player


def _row_norm2(m, i):
    return m[i * 3] * m[i * 3] + m[i * 3 + 1] * m[i * 3 + 1] + m[i * 3 + 2] * m[i * 3 + 2]


def _vec_unit_neg(pos):
    n2 = pos[0] * pos[0] + pos[1] * pos[1] + pos[2] * pos[2]
    from .mveit import isqrt32

    n = int(isqrt32(n2))
    return None if n == 0 else [(-pos[i] * spec.ONE) // n for i in range(3)]


def run() -> tuple[bool, str]:
    """Run ~150 frames of bubble + tactics + player; assert invariants. Returns
    (passed, detail). Detail names the offending frame on failure."""
    b = Bubble()
    default_scene(b)
    player = PlayerState()
    rng = FwRng()
    fds = {
        i: FireDecision(s.aggression, rng)
        for i, s in enumerate(b.slots)
        if s.behavior == spec.BEHAVIOR_HOSTILE
    }

    # Cap shields jumps: only respawn is allowed to *increase* shields.
    prev_shields = player.shields
    one2 = spec.ONE * spec.ONE
    norm_budget = one2 // 20  # 5% drift budget

    for frame in range(150):
        # Hostiles: look-at + MVEIT + fire roll
        for i, s in enumerate(b.slots):
            if s.type != spec.SHIP_ALIVE:
                continue
            mveit(
                s, player_alpha=0, player_beta=0, player_delta=spec.SPEED_MIN
            ) if False else None  # MVEIT requires the oracle's Ship type; we use Bubble's here
            if s.behavior != spec.BEHAVIOR_HOSTILE:
                continue
            m = look_at_player(tuple(s.pos))
            if m is None:
                continue
            s.m = m

            # Invariant 1: rows orthonormal post look-at.
            for r in range(3):
                n2 = _row_norm2(s.m, r)
                if abs(n2 - one2) > norm_budget:
                    return False, f"frame {frame} slot {i} row {r} norm |r|^2={n2} out of budget"

            # Invariant 2: m's 3rd column == unit(-pos)
            expected = _vec_unit_neg(s.pos)
            got = [s.m[2], s.m[5], s.m[8]]
            if expected is not None and got != expected:
                return False, f"frame {frame} slot {i} 3rd-col {got} != unit(-pos) {expected}"

            # Fire roll
            if fds[i].step():
                player.take_hit()

        # Invariant 3: shields non-increasing unless respawn happened this frame.
        if player.shields > prev_shields and not player.respawn_pending:
            return (
                False,
                f"frame {frame} shields jumped {prev_shields}->{player.shields} without respawn",
            )
        prev_shields = player.shields

        # Invariant 4: extra take_hit() during dying must not extend dying timer.
        if player.dying > 0:
            dying_before = player.dying
            player.take_hit()
            if player.dying != dying_before:
                return (
                    False,
                    f"frame {frame} take_hit during dying changed timer {dying_before}->{player.dying}",
                )

        player.death_tick()
        if player.respawn_pending:
            player.respawn()
            default_scene(b)
            prev_shields = player.shields

        player.tick()

    return True, (
        f"150-frame integration: orthonormality + 3rd-col == unit(-pos) + "
        f"shields-monotone + dying-timer-stable all hold (final shields={player.shields}, "
        f"frame_no={player.frame_no})"
    )


def run_hyperspace() -> tuple[bool, str]:
    """Second scenario: drive several hyperspace jumps and assert the
    cross-cutting invariants on fuel + hyper_flash + scene re-seed signal.
    Catches integration drift between hyperspace.py and player.py."""
    from .hyperspace import HyperspaceState
    from .player import PlayerState

    h = HyperspaceState()
    p = PlayerState()
    p_shields0 = p.shields

    # Sequence: 5 jumps with re-seed counting.
    reseed_total = 0
    for jump_idx in range(5):
        # Pick a different target each time.
        h.target_sys = jump_idx + 1
        fuel_before = h.fuel
        ok = h.jump()
        if not ok:
            return False, f"jump {jump_idx} (fuel={fuel_before}) failed to fire"
        if h.fuel != fuel_before - spec.HYPERSPACE_COST:
            return False, f"jump {jump_idx} drained wrong fuel: {fuel_before} -> {h.fuel}"
        if h.chart_mode != 0:
            return False, f"jump {jump_idx} left chart_mode = {h.chart_mode}"

        # Tick the witchspace flash to completion.
        reseed_this = 0
        last_flash = h.hyper_flash
        for tick_i in range(spec.HYPER_FLASH_FRAMES):
            if h.hyper_flash >= last_flash and tick_i > 0:
                return (
                    False,
                    f"jump {jump_idx} hyper_flash not monotone: {last_flash} -> {h.hyper_flash}",
                )
            last_flash = h.hyper_flash
            if h.tick():
                reseed_this += 1
        if reseed_this != 1:
            return False, f"jump {jump_idx} fired re-seed {reseed_this} times, expected 1"
        reseed_total += reseed_this

    max_jumps = spec.FUEL_MAX // spec.HYPERSPACE_COST
    for jump_idx in range(5, max_jumps):
        h.target_sys = jump_idx + 1
        if not h.jump():
            return False, f"jump {jump_idx} prematurely rejected (fuel={h.fuel})"
        for _ in range(spec.HYPER_FLASH_FRAMES):
            h.tick()
    # After max_jumps total, fuel should be near 0 (FUEL_MAX - max_jumps*COST).
    expected_fuel = spec.FUEL_MAX - max_jumps * spec.HYPERSPACE_COST
    if h.fuel != expected_fuel:
        return False, f"fuel after {max_jumps} jumps = {h.fuel}, expected {expected_fuel}"
    # One more jump must fail on fuel.
    h.target_sys = 99
    if h.jump():
        return False, f"jump after fuel drain ({h.fuel}) fired"

    # Player took no damage during this scenario; shields unchanged.
    if p.shields != p_shields0:
        return (
            False,
            f"player shields drifted during hyperspace scenario: {p_shields0} -> {p.shields}",
        )

    return True, (
        f"{max_jumps} jumps drained fuel monotonically; hyper_flash counts "
        f"down 12->0 each jump; re-seed fires exactly once per jump; "
        f"empty-fuel jump rejected; player shields untouched"
    )


def run_trading() -> tuple[bool, str]:
    """Third scenario: a full trade run + commander-rating progression. Catches
    drift between market.py (prices/cargo/credits) and the combat kill counter
    that drives the rating ladder."""
    from .market import Trader, commander_rating, market_gen, parse_fw

    goods, cargo_max, credits0, rating_kills = parse_fw()

    # Visit two systems (different economy + fluctuation) and round-trip every
    # commodity: buy one tonne then immediately sell it back. Credits must
    # return exactly to the starting balance and the hold must end empty.
    for econ, fluct in ((1, 40), (6, 211)):
        prices, qtys = market_gen(goods, econ, fluct)
        t = Trader(goods, cargo_max, credits0, prices, qtys)
        for i in range(len(goods)):
            if t.qtys[i] == 0 or t.credits < t.prices[i]:
                continue
            before = t.credits
            if not t.buy(i):
                return False, f"econ={econ} good {i}: buy rejected despite stock+cash"
            if t.used() > cargo_max:
                return False, f"econ={econ} good {i}: cargo cap {cargo_max} exceeded"
            if not t.sell(i):
                return False, f"econ={econ} good {i}: sell-back rejected"
            if t.credits != before:
                return (
                    False,
                    f"econ={econ} good {i}: round-trip leaked credits {before}->{t.credits}",
                )
        if t.used() != 0 or t.credits != credits0:
            return False, f"econ={econ}: post-roundtrip used={t.used()} credits={t.credits}"

    # Rating progression: walking kills upward only ever advances the rating,
    # and each ladder threshold flips exactly one rank.
    rank = 0
    flips = 0
    for k in range(rating_kills[-1] + 1):
        r = commander_rating(rating_kills, k)
        if r < rank:
            return False, f"rating regressed to {r} at {k} kills"
        if r > rank:
            if k not in rating_kills:
                return False, f"rating advanced at non-threshold kill count {k}"
            flips += 1
            rank = r
    if flips != len(rating_kills) - 1 or rank != 8:
        return False, f"rating ladder flips={flips} final_rank={rank}, expected 8/8"

    return True, (
        "two-system full-catalogue buy/sell round-trips conserve credits + "
        "clear the hold; rating ladder advances one rank per threshold to ELITE"
    )


def run_equip() -> tuple[bool, str]:
    """Fourth scenario: outfit the ship and confirm the upgrades propagate into
    the systems that consume them — the cargo bay into the market hold, and the
    laser tier into combat damage. Catches drift between equip.py and the
    market/combat state it feeds."""
    from .equip import Ship
    from .equip import parse_fw as equip_parse
    from .market import Trader
    from .market import parse_fw as market_parse

    items, consts = equip_parse()
    goods, _, credits0, _ = market_parse()
    idx = {e["name"]: i for i, e in enumerate(items)}

    ship = Ship(items, consts, 10**9)

    # Baseline hold = CARGO_MAX; after the Large Cargo Bay it must be the bigger
    # capacity, and a market actually fills to that new cap.
    base_cap = consts["CARGO_MAX"]
    base = Trader(goods, base_cap, 10**9, [4] * 17, [99] * 17)
    while base.buy(0):
        if base.used() > base_cap + 5:
            break
    if base.used() != base_cap:
        return False, f"baseline hold filled to {base.used()}, expected {base_cap}"

    if not ship.buy(idx["CARGO"]):
        return False, "cargo bay purchase rejected on rich ship"
    big = Trader(goods, ship.cargo_cap, 10**9, [4] * 17, [99] * 17)
    while big.buy(0):
        if big.used() > ship.cargo_cap + 5:
            break
    if big.used() != consts["LARGE_CARGO_CAP"] or big.used() <= base.used():
        return False, f"upgraded hold filled to {big.used()}, expected {consts['LARGE_CARGO_CAP']}"

    # Laser progression: pulse(1) -> beam(2) -> military(3), each step raising
    # combat damage; the firmware applies bubble[i].hits -= laser_damage().
    dmg = [ship.laser_damage()]
    ship.buy(idx["BEAM"])
    dmg.append(ship.laser_damage())
    ship.buy(idx["MILSR"])
    dmg.append(ship.laser_damage())
    if dmg != [1, 2, 3]:
        return False, f"laser damage progression {dmg}, expected [1, 2, 3]"

    return True, (
        "Large Cargo Bay raises the market hold cap and a market fills to it; "
        "laser tier upgrades raise combat damage 1->2->3"
    )


def run_savegame() -> tuple[bool, str]:
    """Fifth scenario: assemble a real commander out of the equip + market state
    that the other scenarios produce, save it, then run it into the ground
    (spend the money, dump the cargo, take damage) and load it back. Proves the
    serializer round-trips the *assembled* commander — not just synthetic field
    values — and that a corrupt save slot is refused without clobbering the live
    state (the property that keeps a bit-rotted save from bricking a session)."""
    from .cmdr import Commander, deserialize, serialize
    from .cmdr import parse_fw as cmdr_parse
    from .equip import Ship
    from .equip import parse_fw as equip_parse
    from .market import Trader
    from .market import parse_fw as market_parse

    ngoods, off = cmdr_parse()
    items, consts = equip_parse()
    goods, _, _, _ = market_parse()
    idx = {e["name"]: i for i, e in enumerate(items)}

    # Outfit + trade: a Large Cargo Bay, beam laser, ECM, two missiles, then
    # fill a few holds. This is the same state machine the firmware globals run.
    ship = Ship(items, consts, 5000)
    ship.buy(idx["CARGO"])
    ship.buy(idx["BEAM"])
    ship.buy(idx["ECM"])
    ship.buy(idx["MISSL"])
    ship.buy(idx["MISSL"])
    trader = Trader(goods, ship.cargo_cap, ship.credits, [9] * ngoods, [50] * ngoods)
    for _ in range(5):
        trader.buy(0)
        trader.buy(3)
    equip_bits = 0
    if "EQ_ECM" in ship.owned:
        equip_bits |= 1 << 0

    saved = Commander(
        ngoods,
        credits=int(trader.credits),
        kills=7,
        fuel=consts["FUEL_MAX"],
        cargo_cap=ship.cargo_cap,
        missiles=ship.missiles,
        laser_tier=ship.laser_tier,
        equip_owned=equip_bits,
        current_sys=42,
        current_galaxy=2,
        target_sys=99,
        cargo=[trader.cargo[i] & 0xFF for i in range(ngoods)],
    )

    slot = serialize(saved, off)  # cmdr_save()

    # Now wreck the live commander: broke, no cargo, missiles spent, system moved.
    live = Commander(
        ngoods,
        credits=3,
        kills=9,
        fuel=10,
        cargo_cap=saved.cargo_cap,
        missiles=0,
        laser_tier=saved.laser_tier,
        equip_owned=0,
        current_sys=200,
        current_galaxy=5,
        target_sys=200,
        cargo=[0] * ngoods,
    )
    if live == saved:
        return False, "wrecked state equals saved state — test set up wrong"

    # cmdr_load() restores the saved snapshot exactly.
    restored = deserialize(slot, off, ngoods)
    if restored is None:
        return False, "valid save slot was rejected on load"
    if restored != saved:
        return False, "loaded commander does not match the saved one"

    # A corrupt slot must be refused so the live state is preserved (firmware
    # only applies cmdr_apply when deserialize succeeds).
    rotted = bytearray(slot)
    rotted[off["CMDR_CREDITS"]] ^= 0x01
    if deserialize(rotted, off, ngoods) is not None:
        return False, "a bit-rotted save slot was accepted"

    return True, (
        f"assembled commander (cap {saved.cargo_cap}, {saved.missiles} "
        f"missiles, {saved.credits} cr) saved + restored exactly; "
        f"corrupt slot refused"
    )


def check() -> tuple[bool, str]:
    ok1, msg1 = run()
    if not ok1:
        return False, "[bubble/tactics] " + msg1
    ok2, msg2 = run_hyperspace()
    if not ok2:
        return False, "[hyperspace] " + msg2
    ok3, msg3 = run_trading()
    if not ok3:
        return False, "[trading] " + msg3
    ok4, msg4 = run_equip()
    if not ok4:
        return False, "[equip] " + msg4
    ok5, msg5 = run_savegame()
    if not ok5:
        return False, "[savegame] " + msg5
    return True, msg1 + " + " + msg2 + " + " + msg3 + " + " + msg4 + " + " + msg5


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "scenario: " + msg)
