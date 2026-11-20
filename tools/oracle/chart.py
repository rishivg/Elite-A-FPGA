"""Model demo chart coordinates, nearest-system selection and jump gates."""

from __future__ import annotations

from . import spec
from .galaxy import SYSTEMS_PER_GALAXY, enumerate_galaxy

CHART_X_SCALE = 2
CHART_Y_SCALE = 3
HYPERSPACE_COST = 7
FUEL_MAX = 70


def chart_dot(sys_x: int, sys_y: int) -> tuple[int, int]:
    return (sys_x // CHART_X_SCALE, sys_y // CHART_Y_SCALE)


def all_chart_dots(galaxy_idx: int = 0) -> list[tuple[int, int]]:
    return [chart_dot(s.x, s.y) for s in enumerate_galaxy(galaxy_idx)]


def nearest_system(cx: int, cy: int, galaxy_idx: int = 0) -> int:
    """Linear scan over 256 systems; Manhattan distance; returns the index."""
    dots = all_chart_dots(galaxy_idx)
    best, best_d = 0, 1 << 30
    for i, (dx, dy) in enumerate(dots):
        d = abs(dx - cx) + abs(dy - cy)
        if d < best_d:
            best_d = d
            best = i
    return best


def can_jump(fuel: int, current_sys: int, target_sys: int) -> bool:
    return fuel >= HYPERSPACE_COST and target_sys != current_sys


def check() -> tuple[bool, str]:
    # 1. All 256 chart dots fit within the chart area (0..127, 0..VIEW_H-1).
    dots = all_chart_dots(0)
    if len(dots) != SYSTEMS_PER_GALAXY:
        return False, f"got {len(dots)} dots, expected {SYSTEMS_PER_GALAXY}"
    for i, (dx, dy) in enumerate(dots):
        if not (0 <= dx <= 127):
            return False, f"system {i} dot.x = {dx}, outside [0, 127]"
        if not (0 <= dy < spec.VIEW_H):
            return False, f"system {i} dot.y = {dy}, outside [0, {spec.VIEW_H})"

    # 2. nearest_system is deterministic and finds the actual nearest dot.
    test_cursors = [(64, 48), (10, 10), (120, 80), (0, 0), (127, 95)]
    for cx, cy in test_cursors:
        a = nearest_system(cx, cy)
        b = nearest_system(cx, cy)
        if a != b:
            return False, f"nearest_system not deterministic at ({cx},{cy})"
        # Manual brute search to confirm.
        best, best_d = 0, 1 << 30
        for i, (dx, dy) in enumerate(dots):
            d = abs(dx - cx) + abs(dy - cy)
            if d < best_d:
                best_d = d
                best = i
        if a != best:
            return False, f"nearest at ({cx},{cy}) = {a} but brute search says {best}"

    # 3. Hyperspace gating.
    if can_jump(0, 0, 1):
        return False, "jump allowed with 0 fuel"
    if can_jump(HYPERSPACE_COST - 1, 0, 1):
        return False, f"jump allowed at fuel < {HYPERSPACE_COST}"
    if can_jump(FUEL_MAX, 5, 5):
        return False, "jump allowed when target == current"
    if not can_jump(FUEL_MAX, 0, 1):
        return False, "valid jump rejected"

    return True, (
        f"all 256 chart dots in range; nearest_system deterministic over "
        f"{len(test_cursors)} cursors; hyperspace cost/equal-target gates correct"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "chart: " + msg)
