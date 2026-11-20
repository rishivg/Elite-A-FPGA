"""Bit-accurate Q8.8 fixed-point reference model for the render pipeline.

This models the simplified demo renderer's rounding and truncation in Python/NumPy.
SpinalSim compares its arithmetic with the RTL. These internal consistency checks
do not establish equivalence with the released Elite-A renderer.

Keep FRAC/WIDTH in sync with rtl/common/Fixed.scala.
"""

from __future__ import annotations

import numpy as np

FRAC = 8
WIDTH = 16
ONE = 1 << FRAC
_MASK = (1 << WIDTH) - 1
_SIGN = 1 << (WIDTH - 1)


def to_fixed(x: float) -> int:
    """Float -> Q8.8 integer (wraps to WIDTH bits, two's complement)."""
    return int(round(x * ONE)) & _MASK


def to_float(q: int) -> float:
    q &= _MASK
    if q & _SIGN:
        q -= 1 << WIDTH
    return q / ONE


def fx_mul(a: int, b: int) -> int:
    """Fixed * fixed -> fixed, matching `((a*b) >> FRAC)` then resize in RTL."""
    a = a - (1 << WIDTH) if a & _SIGN else a
    b = b - (1 << WIDTH) if b & _SIGN else b
    return (a * b >> FRAC) & _MASK


def transform(m: np.ndarray, t: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Model->view transform on Q8.8 inputs. m: 3x3, t: 3, v: 3 (all fixed ints)."""
    out = np.zeros(3, dtype=np.int64)
    for r in range(3):
        acc = 0
        for c in range(3):
            acc = (acc + fx_mul(int(m[r, c]), int(v[c]))) & _MASK
        out[r] = (acc + int(t[r])) & _MASK
    return out


def project(focal: int, cx: int, cy: int, v) -> tuple[int, int]:
    """Perspective projection oracle matching rtl/render/Project.scala exactly.

    v = (x_fixed, y_fixed, z_fixed) as Q8.8 ints. Returns integer screen (sx, sy).
    The divider truncates toward zero (abs, divide, re-sign), so we match with abs
    + floor-divide + re-sign rather than Python's floor-divide on negatives.
    """
    x, y, z = (int(c) for c in v)
    z = z if z > 0 else 1  # near-plane clamp, mirrors RTL
    qx = (abs(x) * focal) // z
    qy = (abs(y) * focal) // z
    qxs = -qx if x < 0 else qx
    qys = -qy if y < 0 else qy
    return cx + qxs, cy - qys


if __name__ == "__main__":
    ident = np.array([to_fixed(x) for x in (1, 0, 0, 0, 1, 0, 0, 0, 1)]).reshape(3, 3)
    t = np.array([0, 0, 0])
    v = np.array([to_fixed(3.0), to_fixed(-2.0), to_fixed(5.0)])
    out = transform(ident, t, v)
    print("identity transform:", [to_float(int(x)) for x in out])
