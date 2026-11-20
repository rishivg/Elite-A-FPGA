"""Run all demo model checks; return a nonzero exit status on failure."""

from __future__ import annotations

import sys
import time

from . import (
    blueprint,
    bresenham,
    bubble,
    chart,
    circle,
    cmdr,
    cull,
    desc,
    equip,
    explosion,
    flight,
    font,
    galaxy,
    hyperspace,
    laser,
    market,
    mission,
    mmio,
    mveit,
    names,
    planet,
    player,
    project,
    psg,
    rng,
    scanner,
    scenario,
    shipyard,
    stardust,
    station,
    status,
    sun,
    tactics,
    trig,
    world,
)

MODULES = [
    ("trig", trig),
    ("rng", rng),
    ("mmio", mmio),
    ("font", font),
    ("flight", flight),
    ("project", project),
    ("mveit", mveit),
    ("stardust", stardust),
    ("bresenham", bresenham),
    ("circle", circle),
    ("cull", cull),
    ("blueprint", blueprint),
    ("laser", laser),
    ("explosion", explosion),
    ("tactics", tactics),
    ("player", player),
    ("bubble", bubble),
    ("planet", planet),
    ("sun", sun),
    ("scanner", scanner),
    ("station", station),
    ("galaxy", galaxy),
    ("names", names),
    ("desc", desc),
    ("chart", chart),
    ("hyperspace", hyperspace),
    ("market", market),
    ("equip", equip),
    ("psg", psg),
    ("cmdr", cmdr),
    ("status", status),
    ("world", world),
    ("shipyard", shipyard),
    ("mission", mission),
    ("scenario", scenario),
]


def main() -> int:
    name_w = max(len(n) for n, _ in MODULES)
    failures = 0
    total_ms = 0.0
    print(f"oracle: {len(MODULES)} subsystems")
    print("-" * (name_w + 60))
    for name, mod in MODULES:
        t0 = time.perf_counter()
        try:
            ok, msg = mod.check()
        except Exception as e:
            ok, msg = False, f"check() raised: {e!r}"
        dt = (time.perf_counter() - t0) * 1000
        total_ms += dt
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name:<{name_w}}  {msg}  ({dt:.1f} ms)")
        if not ok:
            failures += 1
    print("-" * (name_w + 60))
    summary = f"{len(MODULES) - failures}/{len(MODULES)} passed in {total_ms:.1f} ms"
    print(("OK   " if failures == 0 else "FAIL ") + summary)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
