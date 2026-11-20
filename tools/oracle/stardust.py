"""Model demo star motion, perspective projection and deterministic respawn."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .mveit import move_view_position
from .project import project
from .rng import FwRng


@dataclass
class Star:
    pos: list[int] = field(default_factory=lambda: [0, 0, 0])


class Sky:
    """Mirrors firmware module-level state (stars[], rng)."""

    def __init__(self, n: int = spec.NSTARS):
        self.n = n
        self.rng = FwRng()
        self.stars = [Star() for _ in range(n)]
        self._init_field()

    def _init_field(self) -> None:
        for i in range(self.n):
            x = self.rng.signed(spec.to_fixed(40))
            y = self.rng.signed(spec.to_fixed(30))
            z = spec.to_fixed(8) + i * spec.to_fixed(5)
            self.stars[i].pos = [x, y, z]

    def step(self, p_alpha: int, p_beta: int, p_delta: int) -> None:
        """One frame of update; no draw (caller can call screen_positions)."""
        for s in self.stars:
            p = s.pos
            move_view_position(p, p_alpha, p_beta, p_delta)
            if p[2] < spec.to_fixed(1.0):
                p[0] = self.rng.signed(spec.to_fixed(40))
                p[1] = self.rng.signed(spec.to_fixed(30))
                p[2] = spec.to_fixed(100)

    def screen_positions(self) -> list[tuple[int, int] | None]:
        out = []
        for s in self.stars:
            if s.pos[2] <= 0:
                out.append(None)
                continue
            sx, sy = project(s.pos[0], s.pos[1], s.pos[2])
            if 0 <= sx <= spec.SCREEN_W - 1 and 0 <= sy <= spec.VIEW_H - 1:
                out.append((sx, sy))
            else:
                out.append(None)
        return out


def check() -> tuple[bool, str]:
    # 1. Initial field has NSTARS distinct positions with z in increasing order.
    sky = Sky()
    zs = [s.pos[2] for s in sky.stars]
    if zs != sorted(zs):
        return False, f"initial z not monotonically increasing: {zs}"

    # 2. With p_delta=2 over many frames, z monotonically decreases for any non-respawning star.
    sky = Sky()
    z_before = [s.pos[2] for s in sky.stars]
    for _ in range(8):
        sky.step(0, 0, p_delta=2)
    for i, s in enumerate(sky.stars):
        # respawned ones jump to Q(100); skip
        if s.pos[2] > spec.to_fixed(50):
            continue
        if s.pos[2] >= z_before[i]:
            return False, f"star {i} z did not decrease ({z_before[i]} -> {s.pos[2]})"

    # 3. After many frames at fastest speed, every star has respawned at least once
    #    (i.e., we've actually exercised the respawn branch).
    sky = Sky()
    respawned = [False] * sky.n
    for _ in range(200):
        prev_z = [s.pos[2] for s in sky.stars]
        sky.step(0, 0, p_delta=spec.SPEED_MAX)
        for i, s in enumerate(sky.stars):
            if s.pos[2] > prev_z[i]:
                respawned[i] = True
    if not all(respawned):
        return False, f"some stars never respawned: {[i for i, r in enumerate(respawned) if not r]}"

    # 4. Projection produces at least one on-screen star for the initial field.
    sky = Sky()
    if not any(p is not None for p in sky.screen_positions()):
        return False, "no initial star projects on-screen"

    return True, "initial layout, z-decrement, respawn cycling, and projection all sane"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "stardust: " + msg)
