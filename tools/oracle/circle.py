"""Midpoint circle rasterizer with symmetry and radius checks."""

from __future__ import annotations

import math


def circle_pixels(cx: int, cy: int, r: int) -> list[tuple[int, int]]:
    """All pixels the HW will plot for a circle of radius r at (cx, cy).
    Includes the natural duplicates at extremes (x == y, y == 0) — fb writes
    are idempotent so they don't matter."""
    if r <= 0:
        return [(cx, cy)]
    out: list[tuple[int, int]] = []
    x, y = r, 0
    err = 1 - r
    while y <= x:
        out.append((cx + x, cy + y))
        out.append((cx - x, cy + y))
        out.append((cx + x, cy - y))
        out.append((cx - x, cy - y))
        out.append((cx + y, cy + x))
        out.append((cx - y, cy + x))
        out.append((cx + y, cy - x))
        out.append((cx - y, cy - x))
        y += 1
        if err < 0:
            err += 2 * y + 1
        else:
            x -= 1
            err += 2 * (y - x) + 1
    return out


def check() -> tuple[bool, str]:
    # 1. Degenerate r=0 -> single pixel.
    if circle_pixels(10, 20, 0) != [(10, 20)]:
        return False, "r=0 didn't return a single pixel"

    # 2. r=1 covers the 4 axis-adjacent pixels (and diagonals at the extreme).
    px = set(circle_pixels(0, 0, 1))
    for must in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        if must not in px:
            return False, f"r=1 circle missing axis pixel {must}"

    # 3. Larger radii: all emitted pixels lie within sqrt-2 * r of the centre,
    # and a "ring band" check — Bresenham circle pixels live in the annulus
    # [r-1, r+1] of the true circle.
    for r in (3, 5, 8, 16, 25):
        pixels = set(circle_pixels(0, 0, r))
        for px_, py_ in pixels:
            d = math.hypot(px_, py_)
            if not (r - 1.0 <= d <= r + 1.0):
                return False, f"r={r}: pixel {(px_, py_)} at distance {d:.2f}, outside [r-1, r+1]"

    # 4. Symmetry: for any (x, y) on the circle, (-x, y), (x, -y), (-x, -y),
    # (y, x), (-y, x), (y, -x), (-y, -x) are also on it.
    for r in (5, 10, 20):
        pixels = set(circle_pixels(0, 0, r))
        for x, y in list(pixels):
            for mirror in [(-x, y), (x, -y), (-x, -y), (y, x), (-y, x), (y, -x), (-y, -x)]:
                if mirror not in pixels:
                    return False, f"r={r}: missing mirror {mirror} of {(x, y)}"

    # 5. Pixel count grows ~ 2*pi*r (loose bound: between 4r and 12r, including duplicates).
    for r in (5, 10, 20):
        n = len(circle_pixels(0, 0, r))
        if not (4 * r <= n <= 12 * r):
            return False, f"r={r}: emitted {n} pixels (outside {4 * r}..{12 * r} band)"

    return True, "r=0 / r=1 / annulus / 8-way symmetry / pixel count band all correct"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "circle: " + msg)
