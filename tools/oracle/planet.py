"""Model demo planet motion, perspective radius and near-plane respawn."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .mveit import move_view_position
from .project import project


@dataclass
class Planet:
    pos: list[int] = field(
        default_factory=lambda: [spec.to_fixed(14), spec.to_fixed(6), spec.to_fixed(120)]
    )
    size: int = spec.to_fixed(18)

    def step(self, p_alpha: int, p_beta: int, p_delta: int) -> bool:
        """Mirror of planet_step_and_draw's update half. Returns True if the
        body crossed the near plane (caller should respawn)."""
        move_view_position(self.pos, p_alpha, p_beta, p_delta)
        return self.pos[2] < spec.to_fixed(2)

    def render_geometry(self) -> tuple[int, int, int] | None:
        """Return (screen_cx, screen_cy, radius_px) the firmware would plot,
        or None if the body is behind the camera. Uses tdiv to match C trunc."""
        if self.pos[2] <= 0:
            return None
        sx, sy = project(self.pos[0], self.pos[1], self.pos[2])
        r = spec.tdiv(spec.FOCAL * self.size, self.pos[2])
        if r < 1:
            r = 1
        if r > 80:
            r = 80
        return (sx, sy, r)


def check() -> tuple[bool, str]:
    p = Planet()
    pos0 = list(p.pos)
    crossed = p.step(0, 0, 0)
    if crossed or p.pos != pos0:
        return False, f"zero-rate step moved planet: pos {pos0} -> {p.pos}"

    p = Planet(pos=[0, 0, spec.to_fixed(100)])
    z_seq = [p.pos[2]]
    for _ in range(150):
        crossed = p.step(0, 0, spec.SPEED_MAX)
        z_seq.append(p.pos[2])
        if crossed:
            break
    if not crossed:
        return False, "planet didn't cross near plane in 150 frames at SPEED_MAX"
    if not all(z_seq[i + 1] < z_seq[i] for i in range(len(z_seq) - 1)):
        return False, "z did not monotonically decrease under forward speed"

    p_far = Planet(pos=[0, 0, spec.to_fixed(60)], size=spec.to_fixed(10))
    p_near = Planet(pos=[0, 0, spec.to_fixed(30)], size=spec.to_fixed(10))
    r_far = p_far.render_geometry()[2]
    r_near = p_near.render_geometry()[2]
    if not (2 * r_far - 1 <= r_near <= 2 * r_far + 1):
        return False, f"radius parallax wrong: far r={r_far}, near r={r_near}, expected ~2x"

    p_back = Planet(pos=[0, 0, -spec.to_fixed(5)])
    if p_back.render_geometry() is not None:
        return False, "behind-camera planet rendered"

    p_close = Planet(pos=[0, 0, spec.to_fixed(1)], size=spec.to_fixed(100))
    r_close = p_close.render_geometry()[2]
    if r_close != 80:
        return False, f"close-planet radius not clamped to 80: {r_close}"

    return True, (
        "zero-step preserves pos; SPEED_MAX crosses near plane and is monotone; "
        "radius scales 2x at half-z; behind-camera = None; radius clamps at 80"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "planet: " + msg)
