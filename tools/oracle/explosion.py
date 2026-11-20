"""Model demo explosion-particle allocation, motion and expiry."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .mveit import move_view_position
from .rng import FwRng


@dataclass
class Particle:
    active: bool = False
    pos: list[int] = field(default_factory=lambda: [0, 0, 0])
    vel: list[int] = field(default_factory=lambda: [0, 0, 0])
    age: int = 0


class ParticlePool:
    """Mirrors firmware parts[] array. Shares the LCG with stardust spawn."""

    def __init__(self, rng: FwRng | None = None) -> None:
        self.parts: list[Particle] = [Particle() for _ in range(spec.NPARTS_MAX)]
        self.rng = rng if rng is not None else FwRng()

    def spawn(self, at_pos: list[int]) -> int:
        """Spawn BOOM_PARTICLES at at_pos; returns the count actually spawned
        (capped by free slots)."""
        spawned = 0
        for p in self.parts:
            if spawned >= spec.BOOM_PARTICLES:
                break
            if p.active:
                continue
            p.active = True
            p.pos = list(at_pos)
            p.vel = [
                self.rng.signed(spec.PART_SPEED),
                self.rng.signed(spec.PART_SPEED),
                self.rng.signed(spec.PART_SPEED),
            ]
            p.age = 0
            spawned += 1
        return spawned

    def step(self, p_alpha: int, p_beta: int, p_delta: int) -> None:
        for p in self.parts:
            if not p.active:
                continue
            p.pos[0] += p.vel[0]
            p.pos[1] += p.vel[1]
            p.pos[2] += p.vel[2]
            move_view_position(p.pos, p_alpha, p_beta, p_delta)
            p.age += 1
            if p.age >= spec.PART_LIFETIME:
                p.active = False

    def active_count(self) -> int:
        return sum(1 for p in self.parts if p.active)


def check() -> tuple[bool, str]:
    # 1. Empty pool spawns exactly BOOM_PARTICLES particles.
    pool = ParticlePool()
    n = pool.spawn([spec.to_fixed(0), spec.to_fixed(0), spec.to_fixed(10)])
    if n != spec.BOOM_PARTICLES:
        return False, f"first spawn produced {n} particles, expected {spec.BOOM_PARTICLES}"
    if pool.active_count() != spec.BOOM_PARTICLES:
        return False, f"active_count={pool.active_count()} after first spawn"

    # 2. All particles share the spawn position.
    for p in pool.parts:
        if not p.active:
            continue
        if p.pos != [spec.to_fixed(0), spec.to_fixed(0), spec.to_fixed(10)]:
            return False, f"spawned particle not at spawn pos: {p.pos}"

    # 3. After one no-player-motion step, every active particle has moved by its vel.
    saved = [(list(p.pos), list(p.vel)) for p in pool.parts if p.active]
    pool.step(0, 0, 0)
    for (pos0, vel0), p in zip(saved, (q for q in pool.parts if q.active or q.age > 0)):
        # Particle may have just expired if PART_LIFETIME=1, which it isn't (16).
        if p.age != 1:
            return False, f"age did not tick: {p.age}"
        expected = [pos0[0] + vel0[0], pos0[1] + vel0[1], pos0[2] + vel0[2]]
        if p.pos != expected:
            return False, f"particle drift: pos={p.pos}, expected {expected}"

    # 4. Player forward speed pulls particles toward the camera.
    pool2 = ParticlePool()
    pool2.spawn([0, 0, spec.to_fixed(20)])
    z_before = [p.pos[2] for p in pool2.parts if p.active]
    pool2.step(0, 0, p_delta=spec.SPEED_MAX)
    for z0, p in zip(z_before, (q for q in pool2.parts if q.age > 0)):
        # pos.z change = vel.z + (-SPEED_MAX*SPEED_STEP). vel.z bounded by PART_SPEED,
        # SPEED_MAX*SPEED_STEP = 12*16 = 192 >> PART_SPEED = 80, so net always negative.
        if p.pos[2] >= z0:
            return False, f"player speed didn't pull particle in: {z0} -> {p.pos[2]}"

    # 5. After PART_LIFETIME steps, every particle has expired and slots are free.
    pool3 = ParticlePool()
    pool3.spawn([0, 0, spec.to_fixed(10)])
    for _ in range(spec.PART_LIFETIME):
        pool3.step(0, 0, 0)
    if pool3.active_count() != 0:
        return False, f"{pool3.active_count()} particles still active after PART_LIFETIME steps"

    # 6. Spawning twice in a row fills then caps at pool capacity.
    pool4 = ParticlePool()
    n1 = pool4.spawn([0, 0, spec.to_fixed(10)])
    n2 = pool4.spawn([0, 0, spec.to_fixed(10)])
    if n1 + n2 > spec.NPARTS_MAX:
        return False, f"oversubscribed pool: {n1} + {n2} > {spec.NPARTS_MAX}"
    if pool4.active_count() != min(2 * spec.BOOM_PARTICLES, spec.NPARTS_MAX):
        return False, f"unexpected active count after double-spawn: {pool4.active_count()}"

    return True, (
        f"spawn ({spec.BOOM_PARTICLES} per ship), positions, integration, "
        f"player-pull, expiry at {spec.PART_LIFETIME}, and pool cap all correct"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "explosion: " + msg)
