"""Model demo station motion and the rotating screen-space hexagon."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .mveit import move_view_position
from .project import project
from .trig import icos, isin


@dataclass
class Station:
    pos: list[int] = field(default_factory=lambda: [0, spec.to_fixed(-2), spec.to_fixed(60)])
    size: int = spec.to_fixed(6)
    phase: int = 0

    def step(self, p_alpha: int, p_beta: int, p_delta: int) -> bool:
        move_view_position(self.pos, p_alpha, p_beta, p_delta)
        return self.pos[2] < spec.to_fixed(2)  # caller should respawn

    def hex_vertices(self) -> list[tuple[int, int]] | None:
        """Project + compute the 6 hex vertices in screen coords, or None if
        the station is behind the camera or its radius rounds to <2 px."""
        if self.pos[2] <= 0:
            return None
        cx, cy = project(self.pos[0], self.pos[1], self.pos[2])
        r = spec.tdiv(spec.FOCAL * self.size, self.pos[2])
        if r < 2:
            return None
        if r > 40:
            r = 40
        verts = []
        for i in range(6):
            ang = (i * 64 // 6 + self.phase) & 63
            sx = cx + (icos(ang) * r) // 256
            sy = cy + (isin(ang) * r) // 256
            verts.append((sx, sy))
        self.phase = (self.phase + 1) & 63
        return verts


def check() -> tuple[bool, str]:
    s = Station()
    verts = s.hex_vertices()
    if verts is None or len(verts) != 6:
        return False, f"hex_vertices = {verts}"
    if len(set(verts)) != 6:
        return False, f"6 verts collapsed: {verts}"

    s = Station(pos=[0, 0, spec.to_fixed(50)])
    crossed = False
    for _ in range(200):
        if s.step(0, 0, spec.SPEED_MAX):
            crossed = True
            break
    if not crossed:
        return False, "station didn't cross near plane at SPEED_MAX in 200 frames"

    s = Station()
    for _ in range(70):
        s.hex_vertices()  # advances phase
    if s.phase < 0 or s.phase > 63:
        return False, f"phase out of [0, 63]: {s.phase}"

    if Station(pos=[0, 0, -spec.to_fixed(5)]).hex_vertices() is not None:
        return False, "behind-camera station rendered"

    s_far = Station(pos=[0, 0, spec.to_fixed(80)], size=spec.to_fixed(8))
    s_near = Station(pos=[0, 0, spec.to_fixed(40)], size=spec.to_fixed(8))

    # Compute first vertex's distance from centre to back out the radius
    def first_radius(st: Station) -> int:
        v = st.hex_vertices()
        cx = spec.CX + spec.tdiv(spec.FOCAL * st.pos[0], st.pos[2])
        cy = spec.CY - spec.tdiv(spec.FOCAL * st.pos[1], st.pos[2])
        return int(round(((v[0][0] - cx) ** 2 + (v[0][1] - cy) ** 2) ** 0.5))

    r_far = first_radius(s_far)
    r_near = first_radius(s_near)
    if not (2 * r_far - 2 <= r_near <= 2 * r_far + 2):
        return False, f"station radius parallax wrong: r_far={r_far}, r_near={r_near}"

    return True, (
        "6 distinct hex vertices; SPEED_MAX crosses near plane; "
        "phase modulo 64; behind-camera = None; radius scales ~2x at half-z"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "station: " + msg)
