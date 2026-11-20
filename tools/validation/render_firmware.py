"""Build PicoRV32 fixture firmware from independently executed kernel results."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .common import HERE, ROOT


def build_mmio_firmware(destination: Path) -> dict:
    rows = [
        list(map(int, line.split()))
        for line in (destination / "cases.tsv").read_text().splitlines()
    ]
    # Natural consecutive faces plus spread-out boundaries for every scale and
    # sign. Also include overflow-mutated XX18 examples explicitly.
    selected = rows[:32] + rows[:: max(1, len(rows) // 128)][:128]
    selected += [r for r in rows if r[4:7] != r[11:14]][:32] + rows[-24:]

    def packed(values):
        return sum(value << (8 * i) for i, value in enumerate(values))

    table = []
    previous = None
    carry_cases = 0
    for row in selected:
        carry = previous is not None and previous[11:14] == row[4:7] and previous[7:10] == row[7:10]
        carry_cases += int(carry)
        table.append(
            "{"
            + ",".join(
                f"0x{v:x}u"
                for v in [
                    packed(row[:4]),
                    packed(row[4:7]),
                    row[7] | row[8] << 8 | row[9] << 16,
                    row[10],
                    packed(row[11:14]),
                    int(carry),
                ]
            )
            + "}"
        )
        previous = row
    matrix_rows = [
        list(map(int, line.split()))
        for line in (destination / "matrix.tsv").read_text().splitlines()
    ]
    selected_matrices = (
        matrix_rows[:32] + matrix_rows[:: max(1, len(matrix_rows) // 128)][:128] + matrix_rows[-16:]
    )
    pipeline_rows = [
        list(map(int, line.split()))
        for line in (destination / "pipeline.tsv").read_text().splitlines()
    ]

    def words(values):
        return [packed(values[i : i + 4]) for i in range(0, len(values), 4)]

    def c_table(name, items):
        return (
            f"static const u32 {name}[][ {len(items[0])} ] = {{\n"
            + ",\n".join("{" + ",".join(f"0x{v:x}u" for v in item) + "}" for item in items)
            + "\n};\n"
        )

    matrix_table = [
        words(row[:6]) + words(row[6:24]) + words(row[24:30]) for row in selected_matrices
    ]
    pipeline_table = [
        words(row[:6])
        + words(row[6:24])
        + words(row[30:36])
        + [packed(row[24:28]), row[28] << 8 | row[29] << 16, row[36], packed(row[37:40])]
        for row in pipeline_rows
    ]
    extra_tables = c_table("matrices", matrix_table) + c_table("pipelines", pipeline_table)
    raw_transforms = [
        list(map(int, line.split()))
        for line in (destination / "raw-transform.tsv").read_text().splitlines()
    ]
    selected_raw = raw_transforms[:16] + raw_transforms[:: max(1, len(raw_transforms) // 64)][:64]
    raw_table = [
        words(row[19:25]) + words(row[:18]) + words(row[43:49]) + words(row[25:43]) + [row[18]]
        for row in selected_raw
    ]
    raw_pipelines = [
        list(map(int, line.split()))
        for line in (destination / "raw-pipeline.tsv").read_text().splitlines()
    ]
    raw_pipeline_table = [
        words(row[18:24])
        + words(row[:18])
        + words(row[48:54])
        + [packed(row[42:46]), row[46] << 8 | row[47] << 16, row[54], packed(row[55:58])]
        for row in raw_pipelines
    ]
    extra_tables += c_table("raw_matrices", raw_table) + c_table(
        "raw_pipelines", raw_pipeline_table
    )
    raw_vertices = [
        list(map(int, line.split()))
        for line in (destination / "raw-vertex.tsv").read_text().splitlines()
    ]
    selected_vertices = raw_vertices[:8] + raw_vertices[:: max(1, len(raw_vertices) // 56)][:56]
    vertex_table = [
        words(row[18:24])
        + words(row[:18])
        + words(row[51:57])
        + words(row[24:33])
        + words(row[57:65])
        + [packed(row[65:69])]
        + words(row[33:51])
        for row in selected_vertices
    ]
    extra_tables += c_table("vertices", vertex_table)
    source = "// Independent released-code expectations.\n" + "typedef unsigned int u32;\n"
    source += "static const u32 cases[][6] = {\n" + ",\n".join(table) + "\n};\n" + extra_tables
    source += (HERE / "renderer_mmio.c").read_text()
    (destination / "mmio.c").write_text(source)
    subprocess.run(
        [
            "riscv64-unknown-elf-gcc",
            "-march=rv32im",
            "-mabi=ilp32",
            "-nostdlib",
            "-nostartfiles",
            "-ffreestanding",
            "-mno-relax",
            "-Os",
            "-Wall",
            "-Wextra",
            "-I",
            str(ROOT / "fw"),
            "-T",
            str(ROOT / "fw/link.ld"),
            str(ROOT / "fw/crt0.S"),
            str(destination / "mmio.c"),
            "-o",
            str(destination / "mmio.elf"),
        ],
        check=True,
        cwd=ROOT,
        timeout=60,
    )
    subprocess.run(
        [
            "riscv64-unknown-elf-objcopy",
            "-O",
            "binary",
            str(destination / "mmio.elf"),
            str(destination / "mmio.bin"),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "python3",
            str(ROOT / "tools/bin2hex.py"),
            str(destination / "mmio.bin"),
            str(destination / "mmio.hex"),
        ],
        check=True,
        timeout=60,
    )
    if (destination / "mmio.bin").stat().st_size > 32768:
        raise ValueError("MMIO fixture firmware exceeds SoC RAM")
    if carry_cases < 16:
        raise ValueError("MMIO automatic overflow carry coverage missing")
    return {
        "cases": len(selected),
        "automatic_dot_carry_cases": carry_cases,
        "matrix_cases": len(selected_matrices),
        "connected_pipeline_cases": len(pipeline_rows),
        "raw_transform_cases": len(selected_raw),
        "raw_face_pipeline_cases": len(raw_pipelines),
        "raw_vertex_cases": len(selected_vertices),
    }
