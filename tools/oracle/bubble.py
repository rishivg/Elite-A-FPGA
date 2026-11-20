"""Model demo ship-slot allocation, initialization and scene reset."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .mveit import identity


@dataclass
class Ship:
    type: int = spec.SHIP_EMPTY
    pos: list[int] = field(default_factory=lambda: [0, 0, 0])
    m: list[int] = field(default_factory=lambda: identity().copy())
    alpha: int = 0
    beta: int = 0
    delta: int = 0
    hits: int = 0
    boom_age: int = 0
    behavior: int = spec.BEHAVIOR_STATIC
    aggression: int = 0
    fire_cooldown: int = 0
    fire_visual: int = 0


class Bubble:
    """Mirror of `ship_t bubble[NSHIPS_MAX]`."""

    def __init__(self) -> None:
        self.slots: list[Ship] = [Ship() for _ in range(spec.NSHIPS_MAX)]

    def nwshp(
        self,
        px: int,
        py: int,
        pz: int,
        behavior: int = spec.BEHAVIOR_STATIC,
        aggression: int = 0,
        hits: int = spec.SHIP_HITS_INIT,
    ) -> int:
        for i, s in enumerate(self.slots):
            if s.type == spec.SHIP_EMPTY:
                s.type = spec.SHIP_ALIVE
                s.pos = [px, py, pz]
                s.m = identity().copy()
                s.alpha = 0
                s.beta = 0
                s.delta = 0
                s.hits = hits
                s.boom_age = 0
                s.behavior = behavior
                s.aggression = aggression
                s.fire_cooldown = 0
                s.fire_visual = 0
                return i
        return -1

    def res2(self) -> None:
        for s in self.slots:
            s.type = spec.SHIP_EMPTY

    def count(self) -> int:
        return sum(1 for s in self.slots if s.type != spec.SHIP_EMPTY)

    def hostile_count(self) -> int:
        return sum(
            1
            for s in self.slots
            if s.type == spec.SHIP_ALIVE and s.behavior == spec.BEHAVIOR_HOSTILE
        )


def default_scene(b: Bubble) -> None:
    """Mirror of fw/main.c:scene_init — 10 static + 2 hostile."""
    b.res2()
    for _ in range(10):
        b.nwshp(0, 0, spec.to_fixed(30), behavior=spec.BEHAVIOR_STATIC, hits=2)
    b.nwshp(
        spec.to_fixed(9),
        spec.to_fixed(-4),
        spec.to_fixed(45),
        behavior=spec.BEHAVIOR_HOSTILE,
        aggression=40,
        hits=spec.SHIP_HITS_HOSTILE,
    )
    b.nwshp(
        spec.to_fixed(-8),
        spec.to_fixed(6),
        spec.to_fixed(62),
        behavior=spec.BEHAVIOR_HOSTILE,
        aggression=40,
        hits=spec.SHIP_HITS_HOSTILE,
    )


def check() -> tuple[bool, str]:
    # 1. Fresh bubble is empty.
    b = Bubble()
    if b.count() != 0:
        return False, f"fresh bubble has {b.count()} ships"

    # 2. Fill exactly NSHIPS_MAX, then 13th returns -1.
    indices = [b.nwshp(0, 0, spec.to_fixed(5)) for _ in range(spec.NSHIPS_MAX)]
    if indices != list(range(spec.NSHIPS_MAX)):
        return False, f"nwshp didn't fill slots in order: {indices}"
    overflow = b.nwshp(0, 0, spec.to_fixed(5))
    if overflow != -1:
        return False, f"NSHIPS_MAX+1 nwshp returned {overflow}, expected -1"

    # 3. res2 flushes.
    b.res2()
    if b.count() != 0:
        return False, f"res2 left {b.count()} ships"

    # 4. Per-slot fields are populated correctly.
    slot = b.nwshp(
        spec.to_fixed(3),
        spec.to_fixed(-1),
        spec.to_fixed(20),
        behavior=spec.BEHAVIOR_HOSTILE,
        aggression=42,
        hits=4,
    )
    s = b.slots[slot]
    if s.type != spec.SHIP_ALIVE:
        return False, f"slot type after nwshp = {s.type}, expected SHIP_ALIVE"
    if s.pos != [spec.to_fixed(3), spec.to_fixed(-1), spec.to_fixed(20)]:
        return False, f"slot pos = {s.pos}"
    if (s.behavior, s.aggression, s.hits) != (spec.BEHAVIOR_HOSTILE, 42, 4):
        return False, f"slot behavior/aggression/hits = ({s.behavior},{s.aggression},{s.hits})"
    if (s.fire_cooldown, s.fire_visual, s.boom_age) != (0, 0, 0):
        return False, "transient slot fields not zeroed on nwshp"

    # 5. Default scene composition matches scene_init: 12 ships, 2 hostile.
    b = Bubble()
    default_scene(b)
    if b.count() != spec.NSHIPS_MAX:
        return False, f"default_scene populated {b.count()} ships, expected {spec.NSHIPS_MAX}"
    if b.hostile_count() != 2:
        return False, f"default_scene has {b.hostile_count()} hostiles, expected 2"

    return True, (
        f"NSHIPS_MAX={spec.NSHIPS_MAX} fill/overflow/res2/field-init "
        f"+ default scene (10 static + 2 hostile) all correct"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "bubble: " + msg)
