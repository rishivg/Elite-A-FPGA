"""Model demo radar coordinates and hostile markers using C-style division."""

from __future__ import annotations

from . import spec

SCAN_RANGE_Q = spec.to_fixed(spec.SCAN_RANGE)


def scanner_pixel(ship_pos: tuple[int, int, int]) -> tuple[int, int] | None:
    """Return (sx, sy) on the radar, or None if out-of-range or behind camera.
    Mirrors the firmware's range + clip checks exactly (note: C-style trunc
    division for sign-correct mapping of negative x)."""
    x, y, z = ship_pos
    if z <= 0 or z > SCAN_RANGE_Q:
        return None
    if abs(x) > SCAN_RANGE_Q:
        return None
    sx = spec.SCAN_CX + spec.tdiv(x * spec.SCAN_HW, SCAN_RANGE_Q)
    sy = spec.SCAN_CY - spec.tdiv(z * spec.SCAN_HH, SCAN_RANGE_Q)
    if not (spec.SCAN_CX - spec.SCAN_HW <= sx <= spec.SCAN_CX + spec.SCAN_HW):
        return None
    if not (spec.SCAN_CY - spec.SCAN_HH <= sy <= spec.SCAN_CY + spec.SCAN_HH):
        return None
    return (sx, sy)


def hostile_marker_pixels(sx: int, sy: int) -> set[tuple[int, int]]:
    """The 5-pixel cross the firmware draws for hostile ships."""
    return {(sx, sy), (sx - 1, sy), (sx + 1, sy), (sx, sy - 1), (sx, sy + 1)}


def check() -> tuple[bool, str]:
    # 1. Ship directly ahead at mid-range projects to scanner centre column,
    #    top edge.
    p = (0, 0, spec.to_fixed(spec.SCAN_RANGE))
    px = scanner_pixel(p)
    if px is None:
        return False, "max-range ship dead ahead returned None"
    if px[0] != spec.SCAN_CX:
        return False, f"dead-ahead ship not on scanner centre column: sx={px[0]}"
    # At max range, sy should be at the top edge of the radar box.
    if px[1] != spec.SCAN_CY - spec.SCAN_HH:
        return (
            False,
            f"max-range ship not at top of scanner: sy={px[1]} (expected {spec.SCAN_CY - spec.SCAN_HH})",
        )

    # 2. Ship at z=0 (right at camera) → None (z <= 0 reject).
    if scanner_pixel((0, 0, 0)) is not None:
        return False, "ship at z=0 not rejected"

    # 3. Ship behind camera → None.
    if scanner_pixel((0, 0, -spec.to_fixed(5))) is not None:
        return False, "behind-camera ship not rejected"

    # 4. Ship beyond range → None.
    if scanner_pixel((0, 0, spec.to_fixed(spec.SCAN_RANGE + 5))) is not None:
        return False, "out-of-range ship not rejected"

    # 5. Symmetric mapping: +x and -x of the same magnitude reflect over centre.
    p_r = scanner_pixel((spec.to_fixed(10), 0, spec.to_fixed(20)))
    p_l = scanner_pixel((-spec.to_fixed(10), 0, spec.to_fixed(20)))
    if p_r is None or p_l is None:
        return False, "symmetric pair off-radar"
    if (p_r[0] - spec.SCAN_CX) != -(p_l[0] - spec.SCAN_CX):
        return False, f"asymmetric scanner mapping: r={p_r}, l={p_l}"

    # 6. Hostile marker is exactly the 5-pixel cross.
    marker = hostile_marker_pixels(50, 110)
    if marker != {(50, 110), (49, 110), (51, 110), (50, 109), (50, 111)}:
        return False, f"hostile marker wrong: {marker}"

    return True, (
        "dead-ahead max-range maps to top-centre; z<=0 / behind / out-of-range "
        "all rejected; +/-x mapping is symmetric; hostile marker is the 5-pixel cross"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "scanner: " + msg)
