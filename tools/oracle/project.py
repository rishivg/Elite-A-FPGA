"""Model demo perspective division, including truncation toward zero."""

from __future__ import annotations

from . import spec


def project(
    x: int, y: int, z: int, focal: int = spec.FOCAL, cx: int = spec.CX, cy: int = spec.CY
) -> tuple[int, int]:
    """Q8.8 view-space (x, y, z) -> integer screen (sx, sy). Mirrors HW."""
    if z <= 0:
        z = 1
    qx = (abs(x) * focal) // z
    qy = (abs(y) * focal) // z
    if x < 0:
        qx = -qx
    if y < 0:
        qy = -qy
    return cx + qx, cy - qy


def check() -> tuple[bool, str]:
    # 1. On-axis vertex projects to centre.
    sx, sy = project(0, 0, spec.to_fixed(5.0))
    if (sx, sy) != (spec.CX, spec.CY):
        return False, f"on-axis projects to ({sx},{sy}), expected ({spec.CX},{spec.CY})"

    # 2. Mirror symmetry across the centre.
    cases = [(1.0, 1.0, 5.0), (2.0, -3.0, 7.0), (0.25, -0.75, 2.0)]
    for x, y, z in cases:
        xf, yf, zf = spec.to_fixed(x), spec.to_fixed(y), spec.to_fixed(z)
        sx_p, sy_p = project(xf, yf, zf)
        sx_n, sy_n = project(-xf, -yf, zf)
        if (sx_p - spec.CX) != -(sx_n - spec.CX) or (sy_p - spec.CY) != -(sy_n - spec.CY):
            return False, f"symmetry broken at ({x},{y},{z}): +{sx_p, sy_p} / -{sx_n, sy_n}"

    # 3. Twice-as-close = twice the parallax.
    far = project(spec.to_fixed(2.0), 0, spec.to_fixed(10.0))
    near = project(spec.to_fixed(2.0), 0, spec.to_fixed(5.0))
    if (near[0] - spec.CX) != 2 * (far[0] - spec.CX):
        return False, f"parallax not 2x at half-z: near={near[0] - spec.CX} far={far[0] - spec.CX}"

    # 4. Near-plane clamp: z=0 doesn't divide-by-zero, gets clamped to z=1.
    sx, _ = project(spec.to_fixed(1.0), 0, 0)
    sx_clamped, _ = project(spec.to_fixed(1.0), 0, 1)
    if sx != sx_clamped:
        return False, f"z=0 not clamped: sx={sx} vs clamped sx={sx_clamped}"

    return True, "centre/symmetry/parallax/near-clamp all match HW formula"


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "project: " + msg)
