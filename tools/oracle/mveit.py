"""Model demo Q8.8 ship rotation, translation and matrix normalization."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .trig import icos, isin


def v3_rot_plane(p: list[int], i: int, j: int, k: int) -> None:
    """In place: p[i]' = c*p[i] + s*p[j]; p[j]' = -s*p[i] + c*p[j]."""
    c, s = icos(k), isin(k)
    a = spec.fxmul(c, p[i]) + spec.fxmul(s, p[j])
    b = -spec.fxmul(s, p[i]) + spec.fxmul(c, p[j])
    p[i], p[j] = a, b


def move_view_position(position: list[int], roll: int, pitch: int, throttle: int) -> None:
    """Transform a world position by the player's camera motion."""
    if pitch:
        v3_rot_plane(position, 1, 2, -pitch)
    if roll:
        v3_rot_plane(position, 0, 1, -roll)
    position[2] -= throttle * spec.SPEED_STEP


def m_rot_rows(m: list[int], i: int, j: int, k: int) -> None:
    """Pre-multiply by R(i,j;k): rows i and j rotated together."""
    c, s = icos(k), isin(k)
    for col in range(3):
        a, b = m[i * 3 + col], m[j * 3 + col]
        m[i * 3 + col] = spec.fxmul(c, a) + spec.fxmul(s, b)
        m[j * 3 + col] = -spec.fxmul(s, a) + spec.fxmul(c, b)


def m_rot_cols(m: list[int], i: int, j: int, k: int) -> None:
    """Post-multiply by R(i,j;k): columns i and j rotated together."""
    c, s = icos(k), isin(k)
    for row in range(3):
        a, b = m[row * 3 + i], m[row * 3 + j]
        m[row * 3 + i] = spec.fxmul(c, a) + spec.fxmul(s, b)
        m[row * 3 + j] = -spec.fxmul(s, a) + spec.fxmul(c, b)


def isqrt32(x: int) -> int:
    """Restoring integer sqrt; matches fw/main.c:isqrt32."""
    x &= 0xFFFFFFFF
    r = 0
    b = 1 << 30
    while b > x:
        b >>= 2
    while b:
        if x >= r + b:
            x -= r + b
            r = (r >> 1) + b
        else:
            r >>= 1
        b >>= 2
    return r


def v3_normalize(v: list[int]) -> None:
    n2 = v[0] * v[0] + v[1] * v[1] + v[2] * v[2]
    if n2 <= 0:
        return
    n = isqrt32(n2)
    if n == 0:
        return
    v[0] = (v[0] * spec.ONE) // n
    v[1] = (v[1] * spec.ONE) // n
    v[2] = (v[2] * spec.ONE) // n


def m_tidy(m: list[int]) -> None:
    for base in (0, 3, 6):
        row = m[base : base + 3]
        v3_normalize(row)
        m[base : base + 3] = row


def identity() -> list[int]:
    return [spec.ONE, 0, 0, 0, spec.ONE, 0, 0, 0, spec.ONE]


@dataclass
class Ship:
    type: int = 0
    pos: list[int] = field(default_factory=lambda: [0, 0, 0])
    m: list[int] = field(default_factory=identity)
    alpha: int = 0
    beta: int = 0
    delta: int = 0


def mveit(s: Ship, p_alpha: int, p_beta: int, p_delta: int) -> None:
    """One step of Elite MVEIT for one ship."""
    if p_beta:
        v3_rot_plane(s.pos, 1, 2, -p_beta)
        m_rot_rows(s.m, 1, 2, -p_beta)
    if p_alpha:
        v3_rot_plane(s.pos, 0, 1, -p_alpha)
        m_rot_rows(s.m, 0, 1, -p_alpha)
    s.pos[2] -= p_delta * spec.SPEED_STEP

    if s.beta:
        m_rot_cols(s.m, 1, 2, s.beta)
    if s.alpha:
        m_rot_cols(s.m, 0, 1, s.alpha)
    if s.delta:
        s.pos[0] += spec.fxmul(s.m[2], s.delta)
        s.pos[1] += spec.fxmul(s.m[5], s.delta)
        s.pos[2] += spec.fxmul(s.m[8], s.delta)

    if p_alpha or p_beta or s.alpha or s.beta:
        m_tidy(s.m)


def _row_norm2_q16(row: list[int]) -> int:
    return row[0] * row[0] + row[1] * row[1] + row[2] * row[2]


def check() -> tuple[bool, str]:
    # 1. Identity is preserved by mveit when all rates are zero.
    s = Ship(
        type=1,
        pos=[spec.to_fixed(3), spec.to_fixed(-2), spec.to_fixed(7)],
        m=identity(),
        alpha=0,
        beta=0,
        delta=0,
    )
    snapshot = (list(s.pos), list(s.m))
    mveit(s, 0, 0, 0)
    if s.pos != snapshot[0] or s.m != snapshot[1]:
        return False, "zero-rate MVEIT mutated state"

    # 2. Player forward speed pulls the world toward camera along -z.
    s = Ship(type=1, pos=[0, 0, spec.to_fixed(10)], m=identity(), alpha=0, beta=0, delta=0)
    for _ in range(8):
        mveit(s, 0, 0, p_delta=2)
    expected_z = spec.to_fixed(10) - 8 * 2 * spec.SPEED_STEP
    if s.pos[2] != expected_z:
        return False, f"player speed integration drifted: pos.z={s.pos[2]}, expected {expected_z}"

    # 3. Ship's own velocity moves it along the +z axis (3rd column of identity).
    s = Ship(type=1, pos=[0, 0, spec.to_fixed(50)], m=identity(), alpha=0, beta=0, delta=20)
    z0 = s.pos[2]
    for _ in range(16):
        mveit(s, 0, 0, p_delta=0)
    z1 = s.pos[2]
    if z1 - z0 != 16 * 20:
        return False, f"ship velocity didn't add 16*20 to z: delta={z1 - z0}"

    s = Ship(type=1, pos=[0, 0, spec.to_fixed(5)], m=identity(), alpha=1, beta=1, delta=0)
    for _ in range(150):
        mveit(s, p_alpha=1, p_beta=1, p_delta=1)
    one2 = spec.ONE * spec.ONE
    for r in range(3):
        n2 = _row_norm2_q16(s.m[r * 3 : r * 3 + 3])
        drift = abs(n2 - one2) / one2
        if drift > 0.05:
            return False, (
                f"row {r} drifted {drift * 100:.2f}% from unit norm (|row|^2={n2}, expected {one2})"
            )

    return True, "identity, player-speed, ship-velocity, and m_tidy drift all within spec"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "mveit: " + msg)
