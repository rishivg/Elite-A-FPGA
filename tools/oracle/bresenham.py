"""Integer line rasterizer and endpoint/octant checks for the demo."""

from __future__ import annotations


def line_pixels(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Octant-general Bresenham; same recurrence as the HW (dy stored negative)."""
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    out = []
    x, y = x0, y0
    # Watchdog matches HW (sized to the coordinate range).
    max_steps = max(dx, -dy) + 2
    while True:
        out.append((x, y))
        if x == x1 and y == y1:
            break
        if len(out) > max_steps:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return out


def check() -> tuple[bool, str]:
    # 1. Degenerate: p0 == p1 -> single pixel.
    px = line_pixels(7, 11, 7, 11)
    if px != [(7, 11)]:
        return False, f"degenerate single-pixel line broken: {px}"

    # 2. Horizontal: every step advances x.
    px = line_pixels(0, 5, 10, 5)
    if len(px) != 11 or any(p[1] != 5 for p in px):
        return False, f"horizontal line wrong: {px}"

    # 3. 45-degree: every step advances x AND y.
    px = line_pixels(0, 0, 5, 5)
    if px != [(i, i) for i in range(6)]:
        return False, f"diagonal line wrong: {px}"

    # 4. Octant flip: end points always included.
    for x0, y0, x1, y1 in [(0, 0, 3, 7), (3, 7, 0, 0), (-2, 4, 5, -3)]:
        px = line_pixels(x0, y0, x1, y1)
        if px[0] != (x0, y0) or px[-1] != (x1, y1):
            return False, f"endpoint missed for {(x0, y0, x1, y1)}: start={px[0]} end={px[-1]}"

    return True, "degenerate/horizontal/diagonal/all-octant endpoints exact"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "bresenham: " + msg)
