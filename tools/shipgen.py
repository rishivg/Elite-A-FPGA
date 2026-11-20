#!/usr/bin/env python3
"""Convert a ship model (data/ships/*.json) into a BRAM init file (build/data/*.mem).

Source-of-truth ship geometry lives as readable JSON: vertices (world units), edges
(vertex index pairs), and faces (vertex indices + outward normal) for backface-based
hidden-line removal. This bakes it into Q8.8 hex words a SpinalHDL `Mem` can load via
`Mem.init` / `$readmemh`.

Usage:  python3 tools/shipgen.py data/ships/cobra_mk3.json build/data/cobra_mk3.mem
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FRAC = 8
MASK = 0xFFFF


def to_fixed_hex(x: float) -> str:
    return f"{(round(x * (1 << FRAC)) & MASK):04x}"


def main(src: str, dst: str) -> None:
    ship = json.loads(Path(src).read_text())
    words: list[str] = []

    # Layout: [n_verts][verts x,y,z...][n_edges][edges a,b...]
    verts = ship["vertices"]
    if not verts or len(verts) > 256 or any(len(v) != 3 for v in verts):
        raise ValueError("Ship requires 1..256 three-coordinate vertices")
    # The checked-in simplified hull stores triangular faces. Derive the same
    # unique undirected edges as Renderer when an explicit wire list is absent.
    if "edges" in ship:
        edges = ship["edges"]
    else:
        edges = set()
        for face in ship["faces"]:
            if len(face) < 3:
                raise ValueError("Ship face requires at least three vertices")
            for a, b in zip(face, face[1:] + face[:1]):
                edges.add(tuple(sorted((a, b))))
        edges = sorted(edges)
    if any(
        len(edge) != 2
        or any(type(i) is not int or not 0 <= i < len(verts) for i in edge)
        or edge[0] == edge[1]
        for edge in edges
    ):
        raise ValueError("Ship edge contains an invalid vertex index")
    words.append(f"{len(verts) & MASK:04x}")
    for v in verts:
        words += [to_fixed_hex(c) for c in v]
    words.append(f"{len(edges) & MASK:04x}")
    for a, b in edges:
        words.append(f"{((a << 8) | b) & MASK:04x}")

    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(words) + "\n")
    print(f"{src} -> {dst}  ({len(verts)} verts, {len(edges)} edges, {len(words)} words)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
