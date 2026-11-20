"""Model demo sun motion and filled-disc scanlines."""

from __future__ import annotations

from .mveit import isqrt32


def sun_disc_pixels(cx: int, cy: int, r: int) -> set[tuple[int, int]]:
    """All pixels the firmware writes for a filled-disc sun of radius r at
    (cx, cy). Includes off-screen pixels — caller is responsible for clipping
    (firmware does row-y view-height check; line plot clips x to screen)."""
    if r < 1:
        return set()
    out: set[tuple[int, int]] = set()
    r2 = r * r
    for dy in range(-r, r + 1):
        w = int(isqrt32(r2 - dy * dy))
        y = cy + dy
        for x in range(cx - w, cx + w + 1):
            out.add((x, y))
    return out


def check() -> tuple[bool, str]:
    import math

    # 1. r=0 produces no pixels (matches firmware early-return).
    if sun_disc_pixels(50, 50, 0) != set():
        return False, "r=0 produced pixels"

    # 2. r=1 produces a small cross-shape.
    px1 = sun_disc_pixels(50, 50, 1)
    expected = {(50, 49), (50, 50), (50, 51), (49, 50), (51, 50)}
    if px1 != expected:
        return False, f"r=1 disc = {sorted(px1)}, expected {sorted(expected)}"

    # 3. Area grows roughly as πr² (loose bound — Bresenham row widths
    #    use integer isqrt, so the count lives slightly below πr²).
    for r in (5, 10, 20):
        n = len(sun_disc_pixels(50, 50, r))
        lower = int(math.pi * r * r * 0.85)
        upper = int(math.pi * r * r * 1.15)
        if not (lower <= n <= upper):
            return False, f"r={r}: pixel count {n} outside expected band [{lower}, {upper}]"

    # 4. All pixels lie within the bounding box [cx-r, cx+r] x [cy-r, cy+r].
    for r in (3, 8, 15):
        for x, y in sun_disc_pixels(50, 50, r):
            if not (50 - r <= x <= 50 + r and 50 - r <= y <= 50 + r):
                return False, f"r={r}: pixel ({x},{y}) outside bounding box"

    # 5. 4-fold symmetry across the centre.
    px = sun_disc_pixels(0, 0, 10)
    for x, y in list(px):
        for mirror in [(-x, y), (x, -y), (-x, -y)]:
            if mirror not in px:
                return False, f"missing mirror {mirror} of {(x, y)}"

    return True, (
        "r=0/1 exact; area ~ π·r² for r in 5/10/20; bounding box clean; "
        "4-fold reflection symmetry holds"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "sun: " + msg)
