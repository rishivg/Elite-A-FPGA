"""Extract original geometry from verified *released* binaries, not DUT models.

Cross-check signed vertices, edge adjacency/visibility and face normals against
the independently annotated source. Keep original units and special face 15;
triangulating these records would lose Elite's hidden-line semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .common import source_root, write_json


def decode_blueprint(data: bytes, offset: int, strict: bool = True) -> dict:
    if offset < 0 or offset + 20 > len(data):
        raise ValueError("Truncated blueprint header")
    header = data[offset : offset + 20]
    if header[8] % 6 or header[12] % 4:
        raise ValueError("Invalid vertex/face count encoding")
    nv, ne, nf = header[8] // 6, header[9], header[12] // 4
    if not nv or not ne or not nf:
        raise ValueError("Empty blueprint geometry")

    def signed_offset(low, high):
        value = low + 256 * high
        return value - 65536 if value >= 32768 else value

    # Some hulls reuse preceding blueprints' edges/faces with negative offsets.
    edge_offset = signed_offset(header[3], header[16])
    face_offset = signed_offset(header[4], header[17])
    external_reads = []

    def section(start, size, name):
        if offset + start < 0 or offset + start + size > len(data):
            if strict:
                raise ValueError("Blueprint geometry outside binary")
            external_reads.append(
                {
                    "section": name,
                    "bank_relative_start": offset + start,
                    "length": size,
                    "note": "Released pointer reads runtime BBC memory outside the ship-bank file; no bytes fabricated",
                }
            )
            return None
        return data[offset + start : offset + start + size]

    vb = section(20, 6 * nv, "vertices")
    eb = section(edge_offset, 4 * ne, "edges")
    fb = section(face_offset, 4 * nf, "faces")

    def coordinates(magnitudes, signs):
        return [(-v if signs & (128 >> axis) else v) for axis, v in enumerate(magnitudes)]

    vertices = []
    edges = []
    faces = []
    anomalies = []
    for i in range(nv if vb is not None else 0):
        v = vb[6 * i : 6 * i + 6]
        vertices.append(
            {
                "position": coordinates(v[:3], v[3]),
                "faces": [v[4] & 15, v[4] >> 4, v[5] & 15, v[5] >> 4],
                "visibility": v[3] & 31,
            }
        )
    for i in range(ne if eb is not None else 0):
        e = eb[4 * i : 4 * i + 4]
        if e[2] % 4 or e[3] % 4 or e[2] // 4 >= nv or e[3] // 4 >= nv:
            if strict:
                raise ValueError("Invalid blueprint edge endpoint")
            anomalies.append(f"edge {i}: invalid endpoint bytes {e[2]},{e[3]}")
        edges.append(
            {
                "vertices": [e[2] // 4, e[3] // 4],
                "faces": [e[1] & 15, e[1] >> 4],
                "visibility": e[0] & 31,
            }
        )
    for i in range(nf if fb is not None else 0):
        f = fb[4 * i : 4 * i + 4]
        faces.append({"normal": coordinates(f[1:], f[0]), "visibility": f[0] & 31})
    for item in vertices + edges:
        if any(f >= nf and f != 15 for f in item["faces"]):
            if strict:
                raise ValueError("Invalid blueprint face adjacency")
            anomalies.append("face adjacency outside declared face table")
    return {
        "vertices": vertices if vb is not None else None,
        "edges": edges if eb is not None else None,
        "faces": faces if fb is not None else None,
        "metadata": {
            "scoop_and_debris": header[0],
            "target_area": int.from_bytes(header[1:3], "little"),
            "max_edge_heap": header[5],
            "gun_vertex_byte_offset": header[6],
            "explosion_count_encoding": header[7],
            "bounty": int.from_bytes(header[10:12], "little"),
            "visibility_distance": header[13],
            "energy": header[14],
            "speed": header[15],
            "normal_scale_power": header[18],
            "laser_power": header[19] >> 3,
            "missiles": header[19] & 7,
        },
        "geometry_sha256": hashlib.sha256(
            (vb or b"")
            + (eb or b"")
            + (fb or b"")
            + json.dumps(external_reads, sort_keys=True).encode()
        ).hexdigest(),
        "anomalies": anomalies,
        "external_reads": external_reads,
        "complete_geometry": not external_reads,
    }


def source_geometry(text: str, name: str) -> dict:
    def rows(section, macro, arity):
        label = name + "_" + section
        seen = set()
        while label not in seen:
            seen.add(label)
            alias = re.search(rf"(?m)^\s*{re.escape(label)}\s*=\s*(SHIP_\w+)", text)
            if not alias:
                break
            label = alias[1]
        else:
            raise ValueError("Cyclic geometry alias")
        match = re.search(rf"(?m)^\.{re.escape(label)}\s*\n(.*?)(?=^\.|\Z)", text, re.S | re.M)
        if not match:
            header = re.search(rf"(?m)^\.{re.escape(name)}\s*\n(.*?)(?=^\.|\Z)", text, re.S | re.M)
            shared = (
                re.search(rf"LO\((SHIP_\w+_{section})\s*-\s*{re.escape(name)}\)", header[1])
                if header
                else None
            )
            if shared:
                match = re.search(
                    rf"(?m)^\.{re.escape(shared[1])}\s*\n(.*?)(?=^\.|\Z)", text, re.S | re.M
                )
        if not match:
            raise ValueError(f"Missing source section {name}_{section}")
        output = []
        for line in match[1].splitlines():
            line = line.split("\\", 1)[0].strip()
            if line.startswith(macro + " "):
                values = [int(x.strip()) for x in line[len(macro) :].split(",")]
                if len(values) != arity:
                    raise ValueError("Invalid source geometry macro")
                output.append(values)
        return output

    return {
        "vertices": [
            {"position": v[:3], "faces": v[3:7], "visibility": v[7]}
            for v in rows("VERTICES", "VERTEX", 8)
        ],
        "edges": [
            {"vertices": e[:2], "faces": e[2:4], "visibility": e[4]}
            for e in rows("EDGES", "EDGE", 5)
        ],
        "faces": [{"normal": f[:3], "visibility": f[3]} for f in rows("FACES", "FACE", 4)],
    }


def extract_blueprints(destination: Path) -> dict:
    root = source_root("elite_a")
    originals = root / "4-reference-binaries/released"
    models = {}
    banks = []
    instances = 0
    for source in sorted((root / "1-source-files/main-sources").glob("elite-ships-?.asm")):
        text = source.read_text()
        bank = source.stem[-1].upper()
        binary = (originals / f"S.{bank}.bin").read_bytes()
        block = re.search(r"(?m)^\.XX21\s*\n(.*?)^\.E%", text, re.S | re.M)
        if not block:
            raise ValueError("Missing blueprint lookup table")
        names = re.findall(r"^\s*EQUW\s+(\w+)\s*", block[1], re.M)
        if len(names) != 31 or len(binary) < 93:
            raise ValueError("Invalid blueprint bank")
        roles = []
        for index, name in enumerate(names):
            pointer = int.from_bytes(binary[index * 2 : index * 2 + 2], "little")
            if name == "0":
                if pointer:
                    raise ValueError("Unexpected nonempty ship slot")
                continue
            data = (originals / "MISSILE.bin").read_bytes() if name == "SHIP_MISSILE" else binary
            offset = 0 if name == "SHIP_MISSILE" else pointer - 0x5600
            if name == "SHIP_MISSILE" and pointer != 0x7F00:
                raise ValueError("Wrong external missile pointer")
            try:
                decoded = decode_blueprint(data, offset, strict=name != "SHIP_SPLINTER")
            except ValueError as error:
                raise ValueError(
                    f"Bank {bank}, slot {index + 1}, {name}, pointer {pointer:#x}: {error}"
                ) from error
            geometry_text = (
                (root / "1-source-files/main-sources/elite-missile.asm").read_text()
                if name == "SHIP_MISSILE"
                else text
            )
            if geometry_text:
                source_data = source_geometry(geometry_text, name)
                for section in ["vertices", "edges", "faces"]:
                    if decoded[section] != source_data[section]:
                        # Released/source-disc Splinter pointers are explicitly
                        # documented as broken. Preserve actual released bytes;
                        # never substitute the separate bug-fix variant.
                        header = binary[offset : offset + 20]
                        known_splinter = (
                            name == "SHIP_SPLINTER"
                            and section != "vertices"
                            and header[3] == 0x5A
                            and header[16] == 0xFE
                            and header[4] == 68
                        )
                        if not known_splinter:
                            raise ValueError(
                                f"Released/source geometry differs: bank {bank} {name} {section}"
                            )
                        decoded["anomalies"].append(
                            f"released Splinter incorrect {section} pointer differs from intended source geometry"
                        )
                if name == "SHIP_SPLINTER":
                    decoded["intended_source_geometry"] = source_data
            key = name + "-" + decoded["geometry_sha256"][:16]
            if key not in models:
                models[key] = {"name": name, **decoded, "instances": []}
            instance = {
                "bank": bank,
                "slot": index + 1,
                "newb_flags": binary[62 + index],
                "pointer": pointer,
                "metadata": decoded["metadata"],
            }
            models[key]["instances"].append(instance)
            roles.append({"slot": index + 1, "model": key})
            instances += 1
        banks.append(
            {"name": bank, "binary_sha256": hashlib.sha256(binary).hexdigest(), "roles": roles}
        )
    result = {
        "passed": True,
        "source": "verified released S.A..S.W and MISSILE binaries",
        "banks": banks,
        "models": models,
        "ship_names": len({m["name"] for m in models.values()}),
        "geometry_variants": len(models),
        "instances": instances,
        "external_runtime_geometry": [
            key for key, m in models.items() if not m["complete_geometry"]
        ],
        "scope": "Reference data fixtures only; DUT geometry, poses and hidden-line rules still differ",
    }
    write_json(destination, result)
    return result


if __name__ == "__main__":
    from .common import CACHE
    from .setup import prepare

    prepare()
    result = extract_blueprints(CACHE / "reference-blueprints.json")
    print(
        f"{result['geometry_variants']} geometry variants, {result['instances']} ship roles verified"
    )
