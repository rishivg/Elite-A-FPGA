"""Model demo shields, energy, heat, damage, death and respawn."""

from __future__ import annotations

from . import spec


class PlayerState:
    """Mirror of fw/main.c globals laser_heat, fire_active, energy,
    player_shields, hit_flash, dying, frame_no."""

    def __init__(self) -> None:
        self.laser_heat = 0
        self.fire_active = False
        self.energy = spec.ENERGY_MAX
        self.shields = spec.SHIELDS_MAX
        self.hit_flash = 0
        self.dying = 0
        self.frame_no = 0
        self.respawn_pending = False

    # --- laser_step + energy ----------------------------------------------
    def laser_step(self, fire_btn: bool) -> None:
        if fire_btn and self.laser_heat < spec.LASER_HEAT_MAX:
            self.fire_active = True
            self.laser_heat += 1
            if self.energy > 0:
                self.energy -= 1
        else:
            self.fire_active = False
            if self.laser_heat > 0:
                self.laser_heat -= 1
            if (self.frame_no & spec.ENERGY_REGEN_MASK) == 0 and self.energy < spec.ENERGY_MAX:
                self.energy += 1

    # --- damage + death ---------------------------------------------------
    def take_hit(self) -> None:
        if self.shields > 0:
            self.shields -= 1
        self.hit_flash = spec.HIT_FLASH_FRAMES
        if self.shields == 0 and self.dying == 0:
            self.dying = spec.DYING_FRAMES

    def death_tick(self) -> None:
        """Mirror of death_tick(); decrements dying and signals respawn at 0."""
        if self.dying > 0:
            self.dying -= 1
            if self.dying == 0:
                self.respawn_pending = True

    def hit_flash_tick(self) -> None:
        """Firmware decrements hit_flash inside draw_hit_flash; we mirror that
        as a separate per-frame call so check() can advance the counter."""
        if self.hit_flash > 0:
            self.hit_flash -= 1

    def respawn(self) -> None:
        self.shields = spec.SHIELDS_MAX
        self.energy = spec.ENERGY_MAX
        self.laser_heat = 0
        self.fire_active = False
        self.hit_flash = 0
        self.respawn_pending = False

    def tick(self) -> None:
        """Advance frame_no; firmware does `frame_no++` at end of main loop."""
        self.frame_no += 1


def check() -> tuple[bool, str]:
    # 1. Idle at full health: no state changes across many frames.
    p = PlayerState()
    for _ in range(20):
        p.laser_step(False)
        p.tick()
    if (p.shields, p.energy, p.laser_heat, p.dying, p.fire_active) != (
        spec.SHIELDS_MAX,
        spec.ENERGY_MAX,
        0,
        0,
        False,
    ):
        return False, f"idle drifted: shields={p.shields} energy={p.energy} heat={p.laser_heat}"

    # 2. Single hit at full health: shields-- and hit_flash set.
    p = PlayerState()
    p.take_hit()
    if p.shields != spec.SHIELDS_MAX - 1 or p.hit_flash != spec.HIT_FLASH_FRAMES or p.dying != 0:
        return False, f"single hit broken: shields={p.shields} flash={p.hit_flash} dying={p.dying}"

    # 3. SHIELDS_MAX hits at full health drive the player to dying.
    p = PlayerState()
    for _ in range(spec.SHIELDS_MAX):
        p.take_hit()
    if p.shields != 0 or p.dying != spec.DYING_FRAMES:
        return False, f"death not triggered: shields={p.shields} dying={p.dying}"

    # 4. Extra hits while dying don't extend the dying timer.
    extra_before = p.dying
    for _ in range(3):
        p.take_hit()
    if p.dying != extra_before:
        return False, f"extra hits during dying retriggered dying: {extra_before} -> {p.dying}"

    # 5. dying ticks down and signals respawn at 0.
    p = PlayerState()
    for _ in range(spec.SHIELDS_MAX):
        p.take_hit()
    for _ in range(spec.DYING_FRAMES - 1):
        p.death_tick()
        if p.respawn_pending:
            return False, "respawn_pending set before dying counted out"
    p.death_tick()
    if not p.respawn_pending:
        return False, "respawn_pending not set after dying countdown"

    # 6. respawn restores all state.
    p.respawn()
    if (p.shields, p.energy, p.laser_heat, p.dying, p.respawn_pending) != (
        spec.SHIELDS_MAX,
        spec.ENERGY_MAX,
        0,
        0,
        False,
    ):
        return (
            False,
            f"respawn left residue: shields={p.shields} energy={p.energy} heat={p.laser_heat}",
        )

    # 7. Held fire drains energy until 0; then idle regens it back to MAX.
    p = PlayerState()
    for _ in range(spec.ENERGY_MAX + 5):
        p.laser_step(True)
        p.tick()
    if p.energy != 0:
        return False, f"held-fire didn't drain energy to 0: {p.energy}"
    # Idle regen: 1 unit per ENERGY_REGEN_MASK+1 = 4 frames -> ENERGY_MAX*4 + slack
    for _ in range((spec.ENERGY_MAX + 1) * (spec.ENERGY_REGEN_MASK + 1)):
        p.laser_step(False)
        p.tick()
    if p.energy != spec.ENERGY_MAX:
        return False, f"idle regen didn't reach MAX: {p.energy}"

    # 8. Held fire saturates laser_heat then lockouts (re-uses laser.py's
    # invariant from a different angle: energy can drain past lockout).
    p = PlayerState()
    for _ in range(spec.LASER_HEAT_MAX):
        p.laser_step(True)
        p.tick()
    if p.laser_heat != spec.LASER_HEAT_MAX:
        return False, f"heat didn't saturate: {p.laser_heat}"
    p.laser_step(True)
    p.tick()
    if p.fire_active:
        return False, "fire_active True at heat lockout"

    return True, (
        "idle/hit/death/respawn flow + energy drain+regen + heat saturate/lockout "
        "all correct; extra hits during dying don't retrigger timer"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "player: " + msg)
