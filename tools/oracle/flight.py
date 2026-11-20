"""Model the demo track and flight_step integer state transitions."""

from __future__ import annotations

from . import spec


def track(cur: int, inp: int, lim: int) -> int:
    """Mirror of fw/main.c:track — held stick steps toward saturation;
    released steps back to 0 by signum."""
    if inp > 0:
        return spec.clampi(cur + 1, -lim, lim)
    if inp < 0:
        return spec.clampi(cur - 1, -lim, lim)
    return cur - spec.signum(cur)


class PlayerState:
    """fw/main.c globals p_alpha / p_beta / p_delta, evolved by flight_step()."""

    def __init__(self, alpha: int = 0, beta: int = 0, delta: int = 4):
        self.alpha = alpha
        self.beta = beta
        self.delta = delta

    def step(self, packed_input: int) -> None:
        roll = _sb(packed_input, 0)
        pitch = _sb(packed_input, 8)
        speed = _sb(packed_input, 16)
        self.alpha = track(self.alpha, roll, spec.ROLL_MAX)
        self.beta = track(self.beta, pitch, spec.PITCH_MAX)
        if speed > 0:
            self.delta = spec.clampi(self.delta + 1, spec.SPEED_MIN, spec.SPEED_MAX)
        elif speed < 0:
            self.delta = spec.clampi(self.delta - 1, spec.SPEED_MIN, spec.SPEED_MAX)


def _sb(w: int, sh: int) -> int:
    v = (w >> sh) & 0xFF
    return v - 256 if v & 0x80 else v


def check() -> tuple[bool, str]:
    # 1. Exhaustive enumeration of track() within firmware ranges.
    LIMS = (spec.ROLL_MAX, spec.PITCH_MAX)
    for lim in LIMS:
        for cur in range(-lim - 2, lim + 3):
            for inp in (-1, 0, 1):
                # Spec-as-code reference (independent re-derivation).
                if inp > 0:
                    want = max(-lim, min(lim, cur + 1))
                elif inp < 0:
                    want = max(-lim, min(lim, cur - 1))
                else:
                    want = cur - (1 if cur > 0 else (-1 if cur < 0 else 0))
                got = track(cur, inp, lim)
                if got != want:
                    return False, f"track({cur},{inp},{lim}): got {got}, expected {want}"

    # 2. Sequence: stick held in one direction saturates within `lim` frames.
    p = PlayerState(alpha=0, beta=0, delta=spec.SPEED_MIN)
    for _ in range(10):
        p.step(spec.pack_input(roll=+1))
    if p.alpha != spec.ROLL_MAX:
        return False, f"held +roll did not saturate to {spec.ROLL_MAX}: got {p.alpha}"

    # 3. Release returns to 0 within |alpha| frames.
    for _ in range(spec.ROLL_MAX + 2):
        p.step(spec.pack_input())
    if p.alpha != 0:
        return False, f"released roll did not return to 0: got {p.alpha}"

    # 4. Speed clamps at min and max.
    p = PlayerState(delta=spec.SPEED_MAX)
    for _ in range(5):
        p.step(spec.pack_input(speed=+1))
    if p.delta != spec.SPEED_MAX:
        return False, f"speed exceeded SPEED_MAX: {p.delta}"
    for _ in range(50):
        p.step(spec.pack_input(speed=-1))
    if p.delta != spec.SPEED_MIN:
        return False, f"speed fell below SPEED_MIN: {p.delta}"

    return True, ("track() exhaustive; saturate/recenter/clamp behaviours all match")


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "flight: " + msg)
