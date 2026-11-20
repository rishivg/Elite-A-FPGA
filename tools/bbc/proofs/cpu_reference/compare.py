"""Check the symbolic C adapter against independently compiled original ticks."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from pycparser import c_ast, c_parser

from tools.bbc import cycle_reference
from tools.bbc.proofs.cpu_reference import frontend
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, write_json


def qualify(count=100000, workspace=Path("/tmp/fpgaleet-c-reference-native")):
    directory = ROOT / "build/bbc/cpu-c-reference-transpiler"
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    original = source.with_name("m6502-original.h")
    from tools.validation.common import digest

    if digest(original) != cycle_reference.locked_assets()[original.name][
        "sha256"
    ] or source.read_text() != cycle_reference.derive(original.read_text()):
        raise ValueError("Maintained C engine is not the exact pinned derivation")
    rtl = frontend.generate(source, directory)
    ast = c_parser.CParser().parse((directory / "expanded.c").read_text())
    switches = []

    class FindSwitch(c_ast.NodeVisitor):
        def visit_Switch(self, node):
            switches.append(node)

    FindSwitch().visit(ast)
    if len(switches) != 1:
        raise ValueError("Changed original decoder switch inventory")
    evaluator = frontend.Frontend(ast)
    legal = []
    for case in switches[0].stmt.block_items:
        if not isinstance(case, c_ast.Case):
            continue
        statements = case.stmts or []
        invalid = bool(
            statements
            and isinstance(statements[0], c_ast.FuncCall)
            and isinstance(statements[0].name, c_ast.ID)
            and statements[0].name.name == "ref_assert"
        )
        if not invalid:
            legal.append(evaluator.case_value(case.expr))
    if not legal or len(legal) != len(set(legal)):
        raise ValueError("Invalid original microcode admission table")
    header = directory / "reference_fields.h"
    fields = " ".join(f"APPLY({name},{width})" for name, width in frontend.FIELDS)
    header.write_text(
        "#pragma once\n#include <array>\n#define REFERENCE_FIELDS(APPLY) "
        + fields
        + "\nstatic constexpr std::array<unsigned, "
        + str(len(legal))
        + "> ValidMicrocodes{"
        + ",".join(map(str, legal))
        + "};\n"
    )
    harness = Path(__file__).with_name("compare.cpp")
    files = [
        Path(__file__),
        Path(frontend.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        source,
        original,
        Path(cycle_reference.__file__),
        cycle_reference.SOURCE / "lock.json",
        rtl,
        header,
        harness,
        Path(shutil.which("verilator")),
        Path(shutil.which("c++")),
    ]
    workspace.mkdir(parents=True, exist_ok=True)
    includes = workspace / "reference-include"
    includes.mkdir(exist_ok=True)
    copied_source = includes / source.name
    copied_fields = includes / header.name
    shutil.copyfile(source, copied_source)
    shutil.copyfile(header, copied_fields)
    if (
        copied_source.read_bytes() != source.read_bytes()
        or copied_fields.read_bytes() != header.read_bytes()
    ):
        raise ValueError("Reference header copy changed")
    files.extend([copied_source, copied_fields])
    before = hashes(files)
    command = [
        "verilator",
        "-O0",
        "--cc",
        "--exe",
        "--build",
        "-j",
        os.environ.get("VERILATOR_JOBS", "1"),
        "--top-module",
        "M6502ReferenceNext",
        "--Mdir",
        str(workspace),
        "--output-split",
        "5000",
        "--output-split-cfuncs",
        "1000",
        "-Wno-fatal",
        "-CFLAGS",
        f"-O1 -std=c++17 -I{includes}",
        str(rtl),
        str(harness),
    ]
    binary = workspace / "VM6502ReferenceNext"
    build_receipt = workspace / "build-receipt.json"
    build_inputs = {
        "files": hashes(
            [
                source,
                rtl,
                header,
                harness,
                copied_source,
                copied_fields,
                Path(shutil.which("verilator")),
                Path(shutil.which("c++")),
            ]
        ),
        "command": command,
    }
    try:
        cached = json.loads(build_receipt.read_text())
    except (OSError, ValueError):
        cached = {}
    build_log = directory / "native-build.log"
    if build_log.is_symlink():
        build_log.unlink()
    if not (
        cached.get("inputs") == build_inputs
        and binary.is_file()
        and cached.get("binary") == hashes([binary])
        and build_log.is_file()
        and cached.get("log") == hashes([build_log])
    ):
        with build_log.open("w") as log:
            subprocess.run(
                command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900
            )
        write_json(
            build_receipt,
            {"inputs": build_inputs, "binary": hashes([binary]), "log": hashes([build_log])},
        )
    run = subprocess.run([str(binary), str(count)], capture_output=True, text=True, timeout=180)
    (directory / "native-comparison.log").write_text(run.stdout + run.stderr)
    stable = before == hashes(files)
    report = {
        "passed": run.returncode == 0 and stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "artifacts": hashes(
            [
                binary,
                directory / "native-build.log",
                directory / "native-comparison.log",
                directory / "expanded.c",
                directory / "extracted.c",
                directory / "generation.json",
            ]
        ),
        "cases": count,
        "admitted_microcodes": len(legal),
        "state_bits": 256,
        "returned_pin_bits": 64,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "scope": "Finite independent one-step comparison of the generated integer-width-explicit symbolic C adapter with the separately compiled unchanged maintained m6502_tick. Its source is verified as the exact pinned upstream header plus the existing documented branch-IRQ/NMI corrections. Arbitrary deterministic seeded state/pin values at every admitted non-asserting microcode position, including all documented/undocumented instruction families, decimal flags and interrupt/RDY/reset inputs. All included state and returned pin bits are compared. No FPGA or game-state injection.",
        "limits": "Adapter investigation only. Finite samples do not prove the frontend correct for all C inputs or establish CPU/game equivalence. Invalid asserting microcodes and C undefined behavior are outside admission; callbacks and unrelated 6510 state are excluded. No universal game equivalence.",
        "upstream_sha256": digest(original),
        "maintained_source_sha256": digest(source),
        "reference_derivation": "Existing cycle_reference.derive: eight branch first-cycle IRQ latches, branch late-poll corrections, and widened aged-NMI mask. No new engine edits.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "native-comparison-result.json", report)
    if not stable:
        raise ValueError("C adapter inputs changed")
    run.check_returncode()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=100000)
    parser.add_argument("--workspace", type=Path, default=Path("/tmp/fpgaleet-c-reference-native"))
    args = parser.parse_args()
    qualify(args.cases, args.workspace)
