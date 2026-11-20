"""Model demo hostile look-at matrices, pursuit and aggression/cooldown rolls."""

from __future__ import annotations

from . import spec
from .mveit import isqrt32
from .rng import FwRng


def _v3_cross(a: list[int], b: list[int]) -> list[int]:
    return [
        spec.fxmul(a[1], b[2]) - spec.fxmul(a[2], b[1]),
        spec.fxmul(a[2], b[0]) - spec.fxmul(a[0], b[2]),
        spec.fxmul(a[0], b[1]) - spec.fxmul(a[1], b[0]),
    ]


def look_at_player(pos: tuple[int, int, int]) -> list[int] | None:
    """Return the 9-entry row-major Q8.8 model->view rotation, or None if the
    ship is too close to the view-up axis to resolve `right` (caller leaves
    m unchanged that frame)."""
    neg = [-pos[0], -pos[1], -pos[2]]
    neg_n2 = neg[0] * neg[0] + neg[1] * neg[1] + neg[2] * neg[2]
    neg_n = int(isqrt32(neg_n2))
    if neg_n == 0:
        return None
    fwd = [(neg[i] * spec.ONE) // neg_n for i in range(3)]

    # right = (0, 1, 0) x fwd  =>  (fwd[2], 0, -fwd[0])
    right = [fwd[2], 0, -fwd[0]]
    rn = int(isqrt32(right[0] * right[0] + right[1] * right[1] + right[2] * right[2]))
    if rn < 16:
        return None  # near-parallel
    right = [(right[i] * spec.ONE) // rn for i in range(3)]

    up = _v3_cross(fwd, right)

    return [
        right[0],
        up[0],
        fwd[0],
        right[1],
        up[1],
        fwd[1],
        right[2],
        up[2],
        fwd[2],
    ]


class FireDecision:
    """Mirror of the ship_ai fire-roll: per-frame, with cooldown gating,
    draw a 16-bit LCG word; fire iff (word & 0x3F) < aggression."""

    def __init__(self, aggression: int, rng: FwRng | None = None):
        self.aggression = aggression
        self.cooldown = 0
        self.rng = rng if rng is not None else FwRng()

    def step(self) -> bool:
        if self.cooldown > 0:
            self.cooldown -= 1
            return False
        word = self.rng.next_word()
        if (word & spec.AGGRESSION_MASK) < self.aggression:
            self.cooldown = spec.FIRE_COOLDOWN
            return True
        return False


def ship_ai_fire(pos: tuple[int, int, int], fd: FireDecision) -> bool:
    """Full ship_ai fire path: gate by distance (must be inside FIRE_RANGE),
    gate by pos.z > 0 (in front of camera), then the LCG roll. Returns True
    iff this frame produces a fire event."""
    if pos[2] <= 0:
        return False
    dist2 = pos[0] * pos[0] + pos[1] * pos[1] + pos[2] * pos[2]
    range_q = spec.to_fixed(spec.ENEMY_FIRE_RANGE)
    if dist2 > range_q * range_q:
        return False
    return fd.step()


def ship_ai_closing_speed(pos: tuple[int, int, int]) -> int:
    """Mirror of the throttle in ship_ai: 6 when far, 2 when close. Range
    boundary uses the squared close-range threshold (matches firmware)."""
    dist2 = pos[0] * pos[0] + pos[1] * pos[1] + pos[2] * pos[2]
    close_q = spec.to_fixed(spec.ENEMY_CLOSE_RANGE)
    return 6 if dist2 > close_q * close_q else 2


def _row(m: list[int], i: int) -> list[int]:
    return m[i * 3 : (i + 1) * 3]


def _col(m: list[int], j: int) -> list[int]:
    return [m[j], m[j + 3], m[j + 6]]


def _dot(a: list[int], b: list[int]) -> int:
    return spec.fxmul(a[0], b[0]) + spec.fxmul(a[1], b[1]) + spec.fxmul(a[2], b[2])


def check() -> tuple[bool, str]:
    ONE = spec.ONE

    for pos in [
        (spec.to_fixed(3), 0, spec.to_fixed(10)),
        (spec.to_fixed(-7), spec.to_fixed(2), spec.to_fixed(20)),
        (0, spec.to_fixed(4), spec.to_fixed(15)),
    ]:
        m = look_at_player(pos)
        if m is None:
            return False, f"look_at_player returned None for non-degenerate pos {pos}"
        # m's 3rd column = (m[2], m[5], m[8]) should equal fwd direction = unit(-pos)
        neg = [-pos[0], -pos[1], -pos[2]]
        n = int(isqrt32(neg[0] * neg[0] + neg[1] * neg[1] + neg[2] * neg[2]))
        expected_fwd = [(neg[i] * ONE) // n for i in range(3)]
        got_fwd = _col(m, 2)
        if got_fwd != expected_fwd:
            return (
                False,
                f"3rd column != unit(-pos) for pos={pos}: got {got_fwd}, want {expected_fwd}",
            )

    # 2. Rows orthonormal to within Q8.8 quantisation.
    m = look_at_player((spec.to_fixed(5), spec.to_fixed(2), spec.to_fixed(12)))
    one2 = ONE * ONE
    for i in range(3):
        n2 = sum(c * c for c in _row(m, i))
        if abs(n2 - one2) > one2 // 50:  # within 2%
            return False, f"row {i} not unit: |r|^2={n2}, expected {one2}"
    # cross-row dots ~ 0
    for i, j in [(0, 1), (1, 2), (0, 2)]:
        d = _dot(_row(m, i), _row(m, j))
        if abs(d) > ONE // 20:  # within ~0.05
            return False, f"rows {i},{j} not orthogonal: dot={d}"

    # 3. Degenerate: ship directly above the player (pos parallel to up=(0,1,0)).
    if look_at_player((0, spec.to_fixed(10), 0)) is not None:
        return False, "directly-above ship did not return degenerate"

    # 4. Fire-decision: aggression=0 never fires; aggression=64 fires when off cooldown.
    fd0 = FireDecision(aggression=0)
    if any(fd0.step() for _ in range(50)):
        return False, "aggression=0 produced a fire"
    fd_full = FireDecision(aggression=64)
    fires = 0
    for _ in range(200):
        if fd_full.step():
            fires += 1
    expected_min = 200 // (spec.FIRE_COOLDOWN + 1)
    if fires < expected_min - 2:
        return False, f"aggression=64 only fired {fires} times in 200, expected >= {expected_min}"

    # 5. Full ship_ai gating — distance + behind-camera + cooldown.
    # Out-of-range: ship past FIRE_RANGE, aggression=64, never fires.
    fd_far = FireDecision(aggression=64)
    far_pos = (0, 0, spec.to_fixed(spec.ENEMY_FIRE_RANGE + 5))
    if any(ship_ai_fire(far_pos, fd_far) for _ in range(100)):
        return False, f"out-of-range ship at z={spec.ENEMY_FIRE_RANGE + 5} fired anyway"

    # Behind camera: pos.z <= 0, never fires.
    fd_behind = FireDecision(aggression=64)
    if any(ship_ai_fire((0, 0, -spec.to_fixed(1)), fd_behind) for _ in range(50)):
        return False, "behind-camera ship fired"

    # In-range AND in-front AND high aggression: fires within ~cooldown frames.
    fd_near = FireDecision(aggression=64)
    near_pos = (0, 0, spec.to_fixed(15))
    first_fire = None
    for f in range(spec.FIRE_COOLDOWN + 3):
        if ship_ai_fire(near_pos, fd_near):
            first_fire = f
            break
    if first_fire is None:
        return False, "in-range high-aggression ship did not fire within cooldown+3 frames"

    # 6. Closing-speed throttle: far -> 6, close -> 2.
    close = (0, 0, spec.to_fixed(spec.ENEMY_CLOSE_RANGE - 2))
    far = (0, 0, spec.to_fixed(spec.ENEMY_CLOSE_RANGE + 5))
    if ship_ai_closing_speed(close) != 2 or ship_ai_closing_speed(far) != 6:
        return False, (
            "closing-speed throttle wrong: "
            f"close={ship_ai_closing_speed(close)} far={ship_ai_closing_speed(far)}"
        )

    return True, (
        "look-at (3rd col = unit(-pos), rows orthonormal, parallel-to-up degenerate) "
        f"+ fire-decision (0/64) + range/behind-camera gates + closing-speed all correct "
        f"(64 aggression -> {fires}/200 with cooldown)"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "tactics: " + msg)
