"""Independent released Elite-A renderer-kernel differential validation.

Natural observations use real keyboard inputs and read-only debug hooks.
Separate isolated kernel fixtures inject operands into unchanged released 1.F.
Neither set substitutes for end-to-end game/framebuffer equivalence.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .blueprints import extract_blueprints
from .common import CACHE, HERE, ROOT, digest, reference_symbols, source_root, publish_latest, write_json
from .render_cases import (
    case_row,
    kernel_cases,
    matrix_cases,
    orientation_cases,
    projection_cases,
    raw_transform_pairs,
    raw_vertex_cases,
    raw_vertex_pairs,
    vertex_cases,
    viewer_face_pairs,
)
from .render_firmware import build_mmio_firmware
from .setup import prepare, verify


def capture(destination: Path) -> dict:
    verification = verify()
    symbols = reference_symbols("flight")
    catalog = extract_blueprints(destination / "blueprints.json")
    originals = source_root("elite_a") / "4-reference-binaries/released"
    binary = originals / "1.F.bin"
    # Confirm routine addresses against verified original bytes before probing.
    for label, opcode in [
        ("LL9", 0xA5),
        ("LL86", 0xB1),
        ("LL88", 0xC4),
        ("LL51", 0xA2),
        ("LL17", 0xA2),
        ("ll91", 0xB5),
    ]:
        if binary.read_bytes()[symbols[label] - 0x11E3] != opcode:
            raise ValueError(f"Wrong released flight symbol: {label}")
    config = {
        "symbols": symbols,
        "jsbeeb": str(source_root("jsbeeb")),
        "disc": str(source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"),
        "output": str(destination / "natural"),
    }
    write_json(destination / "natural-config.json", config)
    with (destination / "natural.log").open("w") as log:
        subprocess.run(
            ["node", str(HERE / "render_reference.mjs"), str(destination / "natural-config.json")],
            cwd=ROOT,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    cases, coverage = kernel_cases(catalog, originals)
    write_json(
        destination / "kernel-config.json",
        {
            **config,
            "cases": cases,
            "flight_binary": str(binary),
            "output": str(destination / "kernel.tsv"),
        },
    )
    with (destination / "kernel.log").open("w") as log:
        subprocess.run(
            [
                "node",
                str(HERE / "render_kernels_reference.mjs"),
                str(destination / "kernel-config.json"),
            ],
            cwd=ROOT,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    observations = json.loads((destination / "natural/faces.json").read_text())
    natural = "".join("\t".join(map(str, case_row(c))) + "\n" for c in observations)
    (destination / "cases.tsv").write_text(natural + (destination / "kernel.tsv").read_text())
    transforms = json.loads((destination / "natural/transforms.json").read_text())
    matrices, matrix_coverage = matrix_cases()
    pipelines = viewer_face_pairs(transforms, observations)
    # Extend natural viewer-to-face pairs with explicit transform overflows,
    # signed zero and face-dot halving through the connected hardware stages.
    for signs in range(8):
        for magnitude in [0, 127, 255]:
            pipelines.append(
                {
                    "vector": [magnitude, 0x35] * 3,
                    "matrix": [255, 0x2A] * 9,
                    "normal": [
                        31 | sum(((signs >> i) & 1) << (7 - i) for i in range(3)),
                        255,
                        255,
                        255,
                    ],
                    "scale": 0,
                    "distance": 0,
                }
            )
    write_json(
        destination / "matrix-config.json",
        {
            **config,
            "cases": matrices,
            "pipelines": pipelines,
            "flight_binary": str(binary),
            "output": str(destination / "matrix-kernel.tsv"),
            "pipeline_output": str(destination / "pipeline.tsv"),
        },
    )
    with (destination / "matrix.log").open("w") as log:
        subprocess.run(
            ["node", str(HERE / "matrix_reference.mjs"), str(destination / "matrix-config.json")],
            cwd=ROOT,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    natural = "".join(
        "\t".join(map(str, [*t["vector"], *t["matrix"], *t["result"]])) + "\n" for t in transforms
    )
    (destination / "matrix.tsv").write_text(
        natural + (destination / "matrix-kernel.tsv").read_text()
    )
    orientations = json.loads((destination / "natural/orientations.json").read_text())
    raw_cases = orientation_cases()
    raw_pairs = raw_transform_pairs(orientations, transforms)
    by_ship = {o["ship"]: o for o in orientations}
    raw_pipelines = [
        {**p, "raw": by_ship[p["ship"]]["raw"], "transpose": 0} for p in pipelines if "ship" in p
    ]
    for signs in range(8):
        for high, low in [(0, 0), (98, 128), (127, 255)]:
            raw_pipelines.append(
                {
                    "raw": [low, high] * 9,
                    "transpose": 0,
                    "vector": [255, 0x35] * 3,
                    "normal": [
                        31 | sum(((signs >> i) & 1) << (7 - i) for i in range(3)),
                        255,
                        255,
                        255,
                    ],
                    "scale": 0,
                    "distance": 0,
                }
            )
    write_json(
        destination / "orientation-config.json",
        {
            **config,
            "cases": raw_cases,
            "transforms": raw_pairs,
            "pipelines": raw_pipelines,
            "flight_binary": str(binary),
            "output": str(destination / "orientation-kernel.tsv"),
            "transform_output": str(destination / "raw-transform.tsv"),
            "pipeline_output": str(destination / "raw-pipeline.tsv"),
        },
    )
    with (destination / "orientation.log").open("w") as log:
        subprocess.run(
            [
                "node",
                str(HERE / "orientation_reference.mjs"),
                str(destination / "orientation-config.json"),
            ],
            cwd=ROOT,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    natural = "".join(
        "\t".join(map(str, [*o["raw"], 0, *o["result"]])) + "\n" for o in orientations
    )
    (destination / "orientation.tsv").write_text(
        natural + (destination / "orientation-kernel.tsv").read_text()
    )
    vertices = json.loads((destination / "natural/vertices.json").read_text())
    raw_vertices = raw_vertex_pairs(raw_pairs, vertices)
    translated = vertices + vertex_cases()
    projections = projection_cases()
    raw_boundaries = raw_vertex_cases()
    write_json(
        destination / "vertex-config.json",
        {
            **config,
            "cases": translated,
            "projections": projections,
            "flight_binary": str(binary),
            "output": str(destination / "vertex.tsv"),
            "projection_output": str(destination / "projection.tsv"),
            "raw_cases": raw_boundaries,
            "raw_output": str(destination / "raw-vertex-boundaries.tsv"),
        },
    )
    with (destination / "vertex.log").open("w") as log:
        subprocess.run(
            ["node", str(HERE / "vertex_reference.mjs"), str(destination / "vertex-config.json")],
            cwd=ROOT,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    raw_rows = [
        [
            *v["raw"],
            *v["vector"],
            *v["world"],
            *v["expected_matrix"],
            *v["rotated"],
            *v["reduced"],
            *v["screen"],
        ]
        for v in raw_vertices
    ]
    (destination / "raw-vertex.tsv").write_text(
        "".join("\t".join(map(str, row)) + "\n" for row in raw_rows)
        + (destination / "raw-vertex-boundaries.tsv").read_text()
    )
    return {
        "natural_faces": len(observations),
        **coverage,
        "cases": len(observations) + len(cases),
        "matrix": {
            "natural_transforms": len(transforms),
            **matrix_coverage,
            "cases": len(transforms) + len(matrices),
            "viewer_face_pipeline_cases": len(pipelines),
            "natural_viewer_face_pairs": len(pipelines) - 24,
        },
        "orientation": {
            "cases": len(orientations) + len(raw_cases),
            "natural_orientations": len(orientations),
            "exhaustive_coordinate_encodings": 65536,
            "raw_transform_pairs": len(raw_pairs),
            "raw_vertex_transforms": sum(p["transpose"] for p in raw_pairs),
            "raw_face_pipelines": len(raw_pipelines),
        },
        "vertex": {
            "cases": len(translated),
            "natural_vertices": len(vertices),
            "exhaustive_world_magnitudes": 65536,
            "projection_cases": len(projections),
            "exhaustive_numerator_divisor_pairs": 65536,
            "raw_vertex_pipelines": len(raw_vertices) + len(raw_boundaries),
            "raw_vertex_boundaries": len(raw_boundaries),
        },
        "flight_binary_sha256": digest(binary),
        "verified_reference": verification,
        "scope": "Released orientation, transform, face, vertex translation/scaling/projection; current gameplay renderer still differs",
    }


def provenance() -> dict:
    paths = [
        *sorted((ROOT / "rtl").rglob("*.scala")),
        *sorted((ROOT / "sim/tb").glob("*.scala")),
        *sorted(HERE.glob("*.py")),
        *sorted(HERE.glob("*.mjs")),
        *sorted(HERE.glob("*.c")),
        HERE / "reference.lock.json",
        *sorted((ROOT / "fw").glob("*.h")),
        ROOT / "fw/crt0.S",
        ROOT / "fw/link.ld",
        ROOT / "third_party/picorv32.v",
        ROOT / "tools/bin2hex.py",
        ROOT / "build.sbt",
    ]
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def verified_result() -> tuple[Path, dict]:
    directory = (CACHE / "render-latest").resolve()
    result = json.loads((directory / "result.json").read_text())
    if not result.get("passed") or not result.get("dut_executed"):
        raise ValueError("Renderer validation did not execute and pass")
    if result.get("input_hashes") != provenance():
        raise ValueError("Renderer validation is stale; rerun make validation-render")
    if digest(directory / "cases.tsv") != result.get("cases_sha256"):
        raise ValueError("Renderer validation cases were modified")
    if not result.get("artifact_hashes"):
        raise ValueError("Renderer artifact hashes missing")
    for name, expected in result.get("artifact_hashes", {}).items():
        if digest(directory / name) != expected:
            raise ValueError(f"Renderer artifact modified: {name}")
    return directory, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument(
        "--prepared",
        action="store_true",
        help="Verify existing pinned reference; omit npm reinstall",
    )
    args = parser.parse_args()
    if not args.prepared:
        prepare()
    parent = CACHE / "render-runs"
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ-")
    destination = Path(tempfile.mkdtemp(prefix=stamp, dir=parent))
    result = {
        "passed": False,
        "dut_executed": False,
        "input_hashes": provenance(),
        "tool_versions": {},
    }
    for name, command in {
        "node": ["node", "--version"],
        "verilator": ["verilator", "--version"],
        "riscv_gcc": ["riscv64-unknown-elf-gcc", "--version"],
    }.items():
        result["tool_versions"][name] = subprocess.run(
            command, check=True, capture_output=True, text=True
        ).stdout.splitlines()[0]
    try:
        result.update(capture(destination))
        result["mmio_cases"] = build_mmio_firmware(destination)
        if not args.capture_only:
            environment = {
                **os.environ,
                "ELITE_FACE_CASES": str(destination / "cases.tsv"),
                "ELITE_ORIENTATION_CASES": str(destination / "orientation.tsv"),
                "ELITE_RAW_TRANSFORMS": str(destination / "raw-transform.tsv"),
                "ELITE_RAW_PIPELINES": str(destination / "raw-pipeline.tsv"),
                "ELITE_VERTEX_CASES": str(destination / "vertex.tsv"),
                "ELITE_PROJECTION_CASES": str(destination / "projection.tsv"),
                "ELITE_RAW_VERTICES": str(destination / "raw-vertex.tsv"),
                "ELITE_MATRIX_CASES": str(destination / "matrix.tsv"),
                "ELITE_PIPELINE_CASES": str(destination / "pipeline.tsv"),
                "ELITE_FACE_FIRMWARE": str(destination / "mmio.hex"),
            }
            with (destination / "rtl.log").open("w") as log:
                result["dut_executed"] = True
                subprocess.run(
                    [
                        "sbt",
                        "testOnly leet.sim.EliteFaceVisibilitySim leet.sim.EliteFaceRegistersSim leet.sim.EliteMatrixVectorSim leet.sim.EliteOrientationSim leet.sim.ElitePoseRegistersSim leet.sim.EliteRawPoseSim leet.sim.EliteVertexSim leet.sim.EliteProjectionSim leet.sim.EliteVertexRegistersSim leet.sim.SocEliteRendererSim",
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=420,
                    check=True,
                )
            if provenance() != result["input_hashes"]:
                raise ValueError("Renderer sources changed during validation")
            result.update(passed=True, dut_executed=True)
        result["cases_sha256"] = digest(destination / "cases.tsv")
        result["artifact_hashes"] = {
            str(p.relative_to(destination)): digest(p)
            for p in sorted(destination.rglob("*"))
            if p.is_file() and p.name != "result.json"
        }
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        raise
    finally:
        write_json(destination / "result.json", result)
        publish_latest(CACHE / "render-latest", destination)
        print(
            f"Renderer kernel: {result.get('cases', 0)} cases; RTL passed={result['passed']}; {destination}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
