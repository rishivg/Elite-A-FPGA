"""Input fixtures and strict natural-chain pairing for released renderer kernels."""

from __future__ import annotations

import random
from pathlib import Path


def case_row(item: dict) -> list[int]:
    signs = sum(bit << axis for axis, bit in enumerate(item["dot_sign"]))
    return [
        *item["normal"],
        *item["dot_magnitude"],
        signs,
        item["scale"],
        item["distance"],
        item["visibility"],
        *item["next_dot_magnitude"],
    ]


def matrix_cases() -> tuple[list[dict], dict]:
    cases = []
    # Exhaust every unsigned multiplication pair in all nine positions. One
    # row has equal signs, so a product error cannot hide through cancellation.
    for a in range(256):
        for b in range(256):
            vector = sum(([a, (a + b + i) & 127] for i in range(3)), [])
            matrix = []
            for row in range(3):
                for column in range(3):
                    negative = 0 if row == 0 else ((b >> (row + column)) & 1)
                    matrix += [b, ((a ^ b ^ column ^ row) & 127) | (negative << 7)]
            cases.append({"vector": vector, "matrix": matrix})
    rng = random.Random(6502)
    for _ in range(8192):
        cases.append(
            {
                "vector": [rng.randrange(256) for _ in range(6)],
                "matrix": [rng.randrange(256) for _ in range(18)],
            }
        )
    # Force every input sign combination and signed zero; preserve low sign
    # byte bits instead of canonicalizing the original representation.
    for vector_sign in range(8):
        for matrix_sign in range(512):
            for magnitude in [0, 255]:
                vector = sum(
                    ([magnitude, 0x55 | (((vector_sign >> i) & 1) << 7)] for i in range(3)), []
                )
                matrix = sum(
                    ([magnitude, 0x2A | (((matrix_sign >> i) & 1) << 7)] for i in range(9)), []
                )
                cases.append({"vector": vector, "matrix": matrix})
    return cases, {
        "exhaustive_multiply_pairs": 65536,
        "random_transforms": 8192,
        "vector_sign_combinations": 8,
        "matrix_sign_combinations": 512,
        "synthetic_cases": len(cases),
    }


def orientation_cases() -> list[dict]:
    cases = []
    # Each of the nine slots traverses every 16-bit raw encoding. Distinct
    # offset markers keep row order and transpose observable in every case.
    for word in range(65536):
        values = [(word + slot * 0x1F3D) & 65535 for slot in range(9)]
        cases.append(
            {
                "raw": sum(([v & 255, v >> 8] for v in values), []),
                "transpose": (word ^ (word >> 8)) & 1,
            }
        )
    rng = random.Random(197)
    for _ in range(128):
        raw = [rng.randrange(256) for _ in range(18)]
        for transpose in [0, 1]:
            cases.append({"raw": raw, "transpose": transpose})
    return cases


def raw_transform_pairs(orientations: list[dict], transforms: list[dict]) -> list[dict]:
    by_ship = {o["ship"]: o for o in orientations}
    pairs = []
    seen = set()
    for transform in transforms:
        original = by_ship.get(transform["ship"])
        if original is None:
            raise ValueError("Transform has no captured raw orientation")
        normal = original["result"]
        transposed = sum((normal[2 * i : 2 * i + 2] for i in [0, 3, 6, 1, 4, 7, 2, 5, 8]), [])
        # The first LL51 call prepares the viewer; later calls transform
        # vertices through LL42's transpose, even when the matrix is symmetric.
        transpose = int(transform["ship"] in seen)
        seen.add(transform["ship"])
        if transform["matrix"] != (transposed if transpose else normal):
            raise ValueError("Natural transform matrix differs from original prepared orientation")
        pairs.append(
            {
                "ship": transform["ship"],
                "raw": original["raw"],
                "transpose": transpose,
                "vector": transform["vector"],
                "expected_matrix": transform["matrix"],
                "expected_result": transform["result"],
            }
        )
    return pairs


def viewer_face_pairs(transforms: list[dict], faces: list[dict]) -> list[dict]:
    first_faces = {f["ship"]: f for f in reversed(faces) if f["face"] == 0}
    first_transforms = {t["ship"]: t for t in reversed(transforms)}
    pairs = []
    for ship, face in sorted(first_faces.items()):
        transform = first_transforms.get(ship)
        if (
            not transform
            or face["dot_magnitude"] != transform["result"][::2]
            or face["dot_sign"] != [v >> 7 for v in transform["result"][1::2]]
        ):
            raise ValueError(
                f"Natural first face did not consume the viewer transform: ship {ship}"
            )
        pairs.append(
            {
                **transform,
                "normal": face["normal"],
                "scale": face["scale"],
                "distance": face["distance"],
            }
        )
    if len(pairs) < 20:
        raise ValueError("Natural viewer-to-face transform coverage missing")
    return pairs


def kernel_cases(catalog: dict, originals: Path) -> tuple[list[dict], dict]:
    normals = set()
    for model in catalog["models"].values():
        if model["faces"] is None:
            continue  # An out-of-bank pointer requires actual runtime RAM.
        instance = model["instances"][0]
        binary = (
            originals
            / ("MISSILE.bin" if model["name"] == "SHIP_MISSILE" else f"S.{instance['bank']}.bin")
        ).read_bytes()
        base = 0 if model["name"] == "SHIP_MISSILE" else instance["pointer"] - 0x5600
        header = binary[base : base + 20]
        start = (base + header[4] + 256 * header[17]) & 65535
        # Python buffer indexes represent the signed bank-relative pointer.
        if start >= 32768:
            start -= 65536
        for i in range(header[12] // 4):
            normal = tuple(binary[start + 4 * i : start + 4 * i + 4])
            if len(normal) != 4:
                raise ValueError("Truncated original normal")
            normals.add(normal)
    original_count = len(normals)
    # Explicit overflow, signed-zero, cancellation and large magnitude cases.
    for signs in range(8):
        flags = 31 | sum(((signs >> axis) & 1) << (7 - axis) for axis in range(3))
        for mags in [
            (0, 0, 0),
            (255, 255, 255),
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (128, 128, 128),
        ]:
            normals.add((flags, *mags))
    rng = random.Random(1984)
    cases = []
    for normal in sorted(normals):
        for scale in [0, 1, 2, 3, 4, 8, 255]:
            for signs in range(8):
                for dots in [
                    (0, 0, 0),
                    (255, 255, 255),
                    tuple(rng.randrange(256) for _ in range(3)),
                ]:
                    cases.append(
                        {
                            "normal": normal,
                            "dot_magnitude": dots,
                            "dot_sign": [(signs >> axis) & 1 for axis in range(3)],
                            "scale": scale,
                            "distance": rng.choice([0, normal[0] & 31, 31]),
                        }
                    )
    # Consecutive face chains that overflow more than once. Their expected
    # results still come exclusively from the original executable routine.
    for signs in range(8):
        flags = 31 | sum(((signs >> axis) & 1) << (7 - axis) for axis in range(3))
        for magnitude in [255, 127, 63]:
            cases.append(
                {
                    "normal": (flags, 255, 255, 255),
                    "dot_magnitude": (magnitude,) * 3,
                    "dot_sign": [(signs >> axis) & 1 for axis in range(3)],
                    "scale": 0,
                    "distance": 0,
                }
            )
    return cases, {
        "original_normal_records": original_count,
        "normal_records": len(normals),
        "scales": [0, 1, 2, 3, 4, 8, 255],
        "sign_combinations": 8,
        "synthetic_cases": len(cases),
    }


def vertex_cases() -> list[dict]:
    cases = []
    # Every 16-bit world magnitude in every axis, with distinct slot markers.
    for word in range(65536):
        values = [(word + marker) & 65535 for marker in [0, 0x7F01, 0xFE03]]
        world = sum(([v & 255, v >> 8, (word >> (i * 2)) & 255] for i, v in enumerate(values)), [])
        rotated = sum(
            ([(word ^ (85 * i)) & 255, ((word >> 8) ^ (0xAA * i)) & 255] for i in range(3)), []
        )
        cases.append({"world": world, "rotated": rotated})
    # Exercise wrapped addition, underflow/sign changes, signed zero and z=4
    # boundaries for every combination of world/offset signs.
    for value in [0, 1, 3, 4, 127, 128, 255, 256, 65534, 65535]:
        for offset in [0, 1, 3, 4, 127, 128, 255]:
            for signs in range(64):
                cases.append(
                    {
                        "world": sum(
                            (
                                [value & 255, value >> 8, 0x35 | (((signs >> i) & 1) << 7)]
                                for i in range(3)
                            ),
                            [],
                        ),
                        "rotated": sum(
                            ([offset, 0x2A | (((signs >> (i + 3)) & 1) << 7)] for i in range(3)), []
                        ),
                    }
                )
    rng = random.Random(256192)
    for _ in range(8192):
        cases.append(
            {
                "world": [rng.randrange(256) for _ in range(9)],
                "rotated": [rng.randrange(256) for _ in range(6)],
            }
        )
    return cases


def projection_cases() -> list[list[int]]:
    # Every numerator/divisor pair on both axes, including denominator zero,
    # LL61's lossy halvings and both screen-sign directions.
    return [
        [a, 0, 0x35 | (((a ^ q) & 1) << 7), 255 - a, 0, 0x2A | (((a ^ q ^ 1) & 1) << 7), q, 0]
        for a in range(256)
        for q in range(256)
    ]


def raw_vertex_cases() -> list[dict]:
    return [
        {
            "raw": [low, high] * 9,
            "vector": [255, 0x35] * 3,
            "world": sum(
                ([value & 255, value >> 8, 0x2A | (((signs >> i) & 1) << 7)] for i in range(3)), []
            ),
        }
        for signs in range(8)
        for value in [0, 1, 65535]
        for high, low in [(0, 0), (98, 128), (127, 255)]
    ]


def raw_vertex_pairs(raw_transforms: list[dict], vertices: list[dict]) -> list[dict]:
    pairs = []
    for vertex in vertices:
        index = vertex["transform"]
        if index < 0 or index >= len(raw_transforms):
            raise ValueError("Vertex transform index outside capture")
        transform = raw_transforms[index]
        if (
            not transform["transpose"]
            or transform["ship"] != vertex["ship"]
            or transform["expected_result"] != vertex["rotated"]
        ):
            raise ValueError("Natural vertex did not consume its transposed LL51 transform")
        pairs.append({**transform, **vertex})
    if len(pairs) < 100:
        raise ValueError("Natural raw vertex coverage missing")
    return pairs
