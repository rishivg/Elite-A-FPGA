"""Check the demo renderer vertex and face tables against its ship specification."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER_PATH = ROOT / "rtl" / "render" / "Renderer.scala"
JSON_PATH = ROOT / "data" / "ships" / "cobra_mk3.json"


# Canonical Cobra-style hull (matches Renderer.shipVerts; mirror manually).
SPEC_VERTS = [
    (0.0, 0.0, 4.0),  # 0 nose
    (1.8, 0.0, 1.5),  # 1 fwd-right
    (3.5, 0.0, -1.0),  # 2 wingtip-right
    (1.2, 0.0, -2.5),  # 3 tail-right
    (-1.2, 0.0, -2.5),  # 4 tail-left
    (-3.5, 0.0, -1.0),  # 5 wingtip-left
    (-1.8, 0.0, 1.5),  # 6 fwd-left
    (0.0, 1.0, -0.3),  # 7 cockpit (top apex)
    (0.0, -0.7, -0.3),  # 8 belly (bottom apex)
]
RING = [0, 1, 2, 3, 4, 5, 6]


def spec_faces() -> list[tuple[int, int, int]]:
    """Re-derive shipFaces the same way Renderer.scala does."""
    out: list[tuple[int, int, int]] = []
    for i in range(len(RING)):
        a = RING[i]
        b = RING[(i + 1) % len(RING)]
        out.append((7, a, b))  # top apex fan
        out.append((8, b, a))  # bottom apex fan (winding flipped; orient() fixes)
    return out


# Match: (x, y, z) literal in the Scala source (Renderer.shipVerts entries).
_VERT_LINE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)


def parse_hdl_verts() -> list[tuple[float, float, float]]:
    """Pull the shipVerts (Double,Double,Double) literals out of Renderer.scala.

    The shipVerts block is the first `Seq(... )` of 3-tuples in the file
    (followed by 'private val ring'). We take the first 9 triples after the
    'shipVerts' keyword.
    """
    src = RENDERER_PATH.read_text()
    after = src.split("shipVerts", 1)[1]
    until = after.split("private val ring", 1)[0]
    triples = _VERT_LINE.findall(until)
    return [(float(x), float(y), float(z)) for x, y, z in triples]


# The extra bipyramid hulls (modelIdx 1..6); the Cobra (modelIdx 0) is shipVerts.
BIPYRAMID_SHIPS = ["sidewinder", "viper", "krait", "canister", "asteroid", "constrictor"]


def parse_bipyramid_rings() -> dict[str, int]:
    """Ring (perimeter-vertex) count of each `val NAME = bipyramid(Seq(...))`."""
    src = RENDERER_PATH.read_text()
    rings: dict[str, int] = {}
    for name in BIPYRAMID_SHIPS:
        m = re.search(
            r"val\s+%s\s*=\s*bipyramid\(\s*Seq\((.*?)\)\s*,\s*-?[\d.]" % name,
            src,
            re.S,
        )
        if not m:
            raise ValueError(f"{name} bipyramid not found in Renderer.scala")
        rings[name] = len(_VERT_LINE.findall(m.group(1)))
    return rings


def model_counts(ring: int) -> tuple[int, int, int]:
    """A bipyramid over an n-ring: n+2 verts, 2n faces, 3n edges."""
    return ring + 2, 2 * ring, 3 * ring


def check() -> tuple[bool, str]:
    # 1. Spec faces have the expected shape.
    faces = spec_faces()
    if len(faces) != 14:
        return False, f"expected 14 faces, got {len(faces)}"
    if len(SPEC_VERTS) != 9:
        return False, f"expected 9 vertices, got {len(SPEC_VERTS)}"

    # 2. HDL verts match the spec list.
    try:
        hdl = parse_hdl_verts()
    except Exception as e:
        return False, f"could not parse Renderer.shipVerts: {e}"
    if len(hdl) != len(SPEC_VERTS):
        return False, f"HDL has {len(hdl)} verts, spec has {len(SPEC_VERTS)}"
    diffs = [(i, hdl[i], SPEC_VERTS[i]) for i in range(len(hdl)) if hdl[i] != SPEC_VERTS[i]]
    if diffs:
        first = diffs[0]
        return False, f"HDL vertex {first[0]} = {first[1]}, spec = {first[2]} ({len(diffs)} differ)"

    # 3. JSON snapshot must match the spec verts AND the spec faces.
    if not JSON_PATH.exists():
        return False, f"JSON snapshot missing: {JSON_PATH}"
    try:
        j = json.loads(JSON_PATH.read_text())
    except Exception as e:
        return False, f"{JSON_PATH.name} not valid JSON: {e}"

    json_verts = [tuple(v) for v in j.get("vertices", [])]
    if json_verts != SPEC_VERTS:
        if len(json_verts) != len(SPEC_VERTS):
            return False, (
                f"{JSON_PATH.name} has {len(json_verts)} verts, spec has "
                f"{len(SPEC_VERTS)} — refresh JSON from HDL"
            )
        for i, (a, b) in enumerate(zip(json_verts, SPEC_VERTS)):
            if a != b:
                return False, f"{JSON_PATH.name} vertex {i} = {a}, spec = {b}"

    json_faces = [tuple(f) for f in j.get("faces", [])]
    if json_faces != faces:
        return False, (
            f"{JSON_PATH.name} faces drift from ring-derived spec "
            f"(got {len(json_faces)}, expected {len(faces)})"
        )

    # 4. Multi-model: parse the extra bipyramid hulls and validate the padded
    #    unified-ROM scheme. The renderer pads every model to (maxV, maxF, maxE)
    #    and a model's padding edges reference its own face index nF, so that ref
    #    must land in a padding (degenerate) face slot — i.e. any model with
    #    fewer than maxE edges must also have fewer than maxF faces.
    try:
        rings = parse_bipyramid_rings()
    except Exception as e:
        return False, f"could not parse the extra ship hulls: {e}"

    # modelIdx 0 = Cobra (ring 7), then the parsed bipyramids in declared order.
    all_rings = {"cobra": len(RING)} | rings
    if len(all_rings) != 7:
        return False, f"expected 7 ship models, parsed {len(all_rings)}"
    counts = {n: model_counts(r) for n, r in all_rings.items()}
    maxV = max(c[0] for c in counts.values())
    maxF = max(c[1] for c in counts.values())
    maxE = max(c[2] for c in counts.values())
    if (maxV, maxF, maxE) != (9, 14, 21):
        return False, f"max counts (V,F,E)=({maxV},{maxF},{maxE}), expected (9,14,21)"
    for name, (nv, nf, ne) in counts.items():
        if ne < maxE and nf >= maxF:
            return False, (
                f"{name} has padding edges (nE={ne}<{maxE}) but no padding "
                f"face (nF={nf}=={maxF}) — padding-edge face ref would draw"
            )

    # The models list must declare them in the modelIdx order the firmware uses.
    src = RENDERER_PATH.read_text()
    m = re.search(r"val models[^=]*=\s*Seq\((.*?)\)\s*\n", src, re.S)
    expect_order = [
        "shipVerts",
        "sidewinder",
        "viper",
        "krait",
        "canister",
        "asteroid",
        "constrictor",
    ]
    order = re.findall("|".join(expect_order), m.group(1)) if m else []
    if order != expect_order:
        return False, f"models list order = {order}, expected {expect_order}"

    summary = ", ".join(f"{n}:{c[0]}v" for n, c in counts.items())
    return True, (
        f"Cobra spec/HDL/JSON agree (9 verts, 14 faces); {len(counts)} models "
        f"padded to ({maxV},{maxF},{maxE}), padding scheme sound [{summary}]"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "blueprint: " + msg)
