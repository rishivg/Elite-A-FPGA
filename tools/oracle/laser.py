"""Model demo laser heat, cooldown and crosshair hit detection."""

from __future__ import annotations

from . import spec
from .project import project


class LaserState:
    """Mirrors firmware globals laser_heat + fire_active."""

    def __init__(self) -> None:
        self.heat: int = 0
        self.active: bool = False

    def step(self, fire_btn: bool) -> bool:
        if fire_btn and self.heat < spec.LASER_HEAT_MAX:
            self.active = True
            self.heat += 1
        else:
            self.active = False
            if self.heat > 0:
                self.heat -= 1
        return self.active


def crosshair_hit(view_pos: tuple[int, int, int]) -> bool:
    """Mirror of fw/main.c:crosshair_hit. view_pos = (x, y, z) Q8.8."""
    x, y, z = view_pos
    if z <= 0:
        return False
    sx, sy = project(x, y, z)
    return (
        spec.CX - spec.CROSS_HALF <= sx <= spec.CX + spec.CROSS_HALF
        and spec.CY - spec.CROSS_HALF <= sy <= spec.CY + spec.CROSS_HALF
    )


def check() -> tuple[bool, str]:
    # 1. Released stick keeps heat at 0 and fire_active False.
    L = LaserState()
    for _ in range(5):
        if L.step(False) or L.heat != 0:
            return False, f"idle drifted: heat={L.heat} active={L.active}"

    # 2. Held fire ramps heat up to MAX then locks out.
    for _ in range(spec.LASER_HEAT_MAX):
        if not L.step(True):
            return False, f"unexpected lockout at heat={L.heat}"
    if L.heat != spec.LASER_HEAT_MAX:
        return False, f"heat did not saturate: {L.heat}, expected {spec.LASER_HEAT_MAX}"
    # one more held step -> lockout
    if L.step(True):
        return False, "fire_active still True at lockout"

    # 3. Releasing cools heat back to 0 within MAX frames.
    for _ in range(spec.LASER_HEAT_MAX + 2):
        L.step(False)
    if L.heat != 0:
        return False, f"heat did not cool to 0: {L.heat}"

    # 4. Crosshair hit predicate: dead-ahead ship at any positive z is a hit.
    for z_units in (3, 10, 22, 50):
        if not crosshair_hit((0, 0, spec.to_fixed(z_units))):
            return False, f"dead-ahead ship at z={z_units} not in crosshair"

    # 5. Behind-camera ship is never a hit.
    if crosshair_hit((0, 0, -spec.to_fixed(1))):
        return False, "ship behind camera reported as hit"

    # 6. Off-axis ship outside crosshair box is missed.
    off = spec.to_fixed(2.0)  # >> CROSS_HALF/FOCAL * z at z=5 -> well outside
    if crosshair_hit((off, 0, spec.to_fixed(5))):
        return False, f"off-axis ship ({spec.to_float(off)} at z=5) reported as hit"

    return True, (
        "heat charge/lockout/cool + crosshair predicate "
        "(dead-ahead at multiple ranges, behind-camera, off-axis) all correct"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "laser: " + msg)
