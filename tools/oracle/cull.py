"""Check signed screen-space triangle winding and degenerate faces."""

from __future__ import annotations


def signed_area(ax: int, ay: int, bx: int, by: int, cx: int, cy: int) -> int:
    """2x the signed area of triangle ABC in screen space."""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def is_front(
    a: tuple[int, int], b: tuple[int, int], c: tuple[int, int], front_positive: bool = True
) -> bool:
    s = signed_area(a[0], a[1], b[0], b[1], c[0], c[1])
    return (s > 0) if front_positive else (s < 0)


def check() -> tuple[bool, str]:
    # 1. CCW triangle has positive area (front_positive convention).
    if not is_front((0, 0), (10, 0), (0, 10)):
        return False, "CCW triangle not front-facing under front_positive=True"

    # 2. CW triangle (same verts reversed) is back-facing.
    if is_front((0, 0), (0, 10), (10, 0)):
        return False, "CW triangle reported front-facing under front_positive=True"

    # 3. Degenerate (collinear) -> area=0 -> back-facing (excluded).
    if is_front((0, 0), (5, 0), (10, 0)):
        return False, "collinear triangle reported front-facing"

    # 4. Translation invariance.
    a, b, c = (3, 4), (15, 4), (3, 14)
    base = is_front(a, b, c)
    shifted = is_front((a[0] + 100, a[1] - 50), (b[0] + 100, b[1] - 50), (c[0] + 100, c[1] - 50))
    if base != shifted:
        return False, "translation changed visibility sign"

    return True, "CCW/CW/degenerate/translation-invariance all correct"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "cull: " + msg)
