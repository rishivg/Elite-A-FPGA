"""Compare the actual C CPU step with its complete combinational RTL using CBMC.

Only synthesis preparation is reusable. Each invocation performs fresh native
checks, export equivalence and CBMC verification; no proof verdict is cached.
"""

import argparse
import shutil
import subprocess
import time
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import aig_c, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def roundtrip_wrapper():
    ports = [("state_in", 256), ("pins_in", 64), ("state_out", 256), ("pins_out", 64)]
    connections = [
        f".\\{name}[{bit}] ({name}[{bit}])" for name, width in ports for bit in range(width)
    ]
    return (
        "module AigRoundtrip(input [255:0] state_in,input [63:0] pins_in,"
        "output [255:0] state_out,output [63:0] pins_out,output trap);\n"
        "Gate g(" + ",".join([*connections, ".trap(trap)"]) + ");\nendmodule\n"
    )


def verified_cbmc(text):
    """Require every named comparison, rather than accepting a success footer alone."""
    expected = ["trap", "returned_pins", *("state_" + n for n, _ in aig_c.FIELDS)]
    return (
        "VERIFICATION SUCCESSFUL" in text
        and "** 0 of " in text
        and all(f" {name}: SUCCESS" in text for name in expected)
        and ": FAILURE" not in text
    )


def run(command, log, timeout):
    with log.open("w") as output:
        return subprocess.run(
            command, stdout=output, stderr=subprocess.STDOUT, timeout=timeout, check=False
        )


def native(directory):
    compilation = run(
        [
            "gcc",
            "-O0",
            "-std=c11",
            "-DNATIVE",
            str(directory / "harness.c"),
            "-o",
            str(directory / "native"),
        ],
        directory / "native-build.log",
        180,
    )
    if compilation.returncode:
        raise ValueError("Native C/AIG compilation failed")
    return run([str(directory / "native")], directory / "native.log", 300)


def qualify(cbmc, work):
    started = time.monotonic()
    cbmc = cbmc.resolve(strict=True)
    premises = [
        ROOT / "build/bbc" / name / "result.json"
        for name in ("cpu-c-structural-pruning", "cpu-c-structural-adapter")
    ]
    pruning, adapter = [memory_composition.sealed(p) for p in premises]
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    model = Path("/tmp/fpgaleet-c-structural-pruning/pruned.v")
    if pruning["artifacts"].get(str(model)) != digest(model):
        raise ValueError("Changed complete structural source model")
    if adapter["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
        raise ValueError("Changed actual C reference header")
    inputs = [
        Path(__file__),
        Path(aig_c.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        *premises,
        source,
        model,
        cbmc,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    work.mkdir(parents=True, exist_ok=True)
    result = ROOT / "build/bbc/cpu-c-independent-translation/result.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    write_json(work / "run-inputs.json", {"inputs": before, "started_epoch": time.time()})
    shutil.copyfile(source, work / "m6502.h")
    aag = work / "symbols.aag"
    command = (
        f"read_verilog -sv {model}; prep -top M6502ReferenceNext; opt; "
        f"techmap; opt; abc -g AND; opt_clean; write_aiger -ascii -symbols {aag}"
    )
    cached = prepared.prepare(work, command, [model], [aag], timeout=1800)
    (work / "aig_eval.c").write_text(aig_c.generate(aag.read_text()))
    (work / "harness.c").write_text(aig_c.harness(source.read_text()))
    (work / "roundtrip-wrapper.v").write_text(roundtrip_wrapper())
    # read_aiger's wideports mode does not merge all indexed symbols in this
    # tool version. Connect every published bit explicitly instead.
    export_query = (
        f"read_verilog -sv {model}; prep -top M6502ReferenceNext; opt; "
        "techmap; opt; abc -g AND; opt_clean; rename M6502ReferenceNext Gold; "
        f"read_aiger -module_name Gate {aag}; read_verilog {work / 'roundtrip-wrapper.v'}; "
        "miter -equiv -flatten Gold AigRoundtrip Miter; prep -top Miter; "
        "opt -full; sat -verify -prove trigger 0"
    )
    export = run(["yosys", "-Q", "-T", "-p", export_query], work / "roundtrip-proof.log", 1800)
    if export.returncode or "SUCCESS!" not in (work / "roundtrip-proof.log").read_text():
        raise ValueError("AIG export did not preserve all input/output bits")
    if native(work).returncode:
        raise ValueError("Independent native C/AIG comparison failed")
    faults = []
    changes = (
        (
            "wrong-zero-page-operation",
            "case (0x05<<3)|2: c->A|=_GD();",
            "case (0x05<<3)|2: c->A&=_GD();",
        ),
        (
            "lost-branch-irq-restoration",
            "case (0x10<<3)|3: c->PC=c->AD;if(c->branch_irq)c->irq_pip|=0x200;",
            "case (0x10<<3)|3: c->PC=c->AD;",
        ),
    )
    for name, old, new in changes:
        case = work / name
        case.mkdir(exist_ok=True)
        (case / "m6502.h").write_text(replace_once(source.read_text(), old, new))
        shutil.copyfile(work / "aig_eval.c", case / "aig_eval.c")
        shutil.copyfile(work / "harness.c", case / "harness.c")
        trial = native(case)
        detected = trial.returncode == 1 and "Failed " in (case / "native.log").read_text()
        faults.append({"name": name, "detected": detected, "control": "native actual-C mutation"})
    case = work / "wrong-exported-address"
    case.mkdir(exist_ok=True)
    lines = aag.read_text().splitlines()
    _, _, _, bindings = aig_c.parse(aag.read_text())
    index = 321 + bindings["o"][("pins_out", 0)]
    lines[index] = str(int(lines[index]) ^ 1)
    (case / "symbols.aag").write_text("\n".join(lines) + "\n")
    (case / "aig_eval.c").write_text(aig_c.generate((case / "symbols.aag").read_text()))
    shutil.copyfile(source, case / "m6502.h")
    shutil.copyfile(work / "harness.c", case / "harness.c")
    trial = native(case)
    faults.append(
        {
            "name": case.name,
            "detected": trial.returncode == 1
            and "Failed returned_pins" in (case / "native.log").read_text(),
            "control": "native AIG output inversion",
        }
    )
    if not all(f["detected"] for f in faults):
        raise ValueError("Independent comparison missed a deliberate fault")
    print("Native transitions, export preservation and fault controls: PASS", flush=True)
    command = [
        str(cbmc),
        str(work / "harness.c"),
        "--unwind",
        "1",
        "--unwinding-assertions",
        "--slice-formula",
    ]
    write_json(work / "cbmc-command.json", command)
    proof = run(command, work / "proof.log", 7200)
    stable = before == hashes(inputs)
    passed = stable and proof.returncode == 0 and verified_cbmc((work / "proof.log").read_text())
    write_json(
        result,
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.rglob("*") if p.is_file()]),
            "preparation_cache_reused": cached,
            "faults": faults,
            "native_transitions": 100000,
            "comparison_assertions": 20,
            "admitted_microsteps": len(aig_c.microcodes(source.read_text())),
            "wall_seconds": time.monotonic() - started,
            "scope": "Actual maintained C m6502_tick compared with the full pruned structural RTL via independently evaluated AIG gates. All state fields and returned pins checked. Arbitrary C-typed state and pins admitted when the microstep is implemented, SYNC replaces it, or RDY holds a read. Fresh CBMC with safety and unwinding assertions; independent all-320-input/all-321-output SAT export preservation. Native faults check comparison sensitivity, not universal fault equivalence.",
            "limits": "One C step in the stated defined-execution domain. C Boolean storage is canonical. Reference-source corrections, actual RTL CPU history/reachable entry, enables/reset, device scheduling and whole-game composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    print("Independent C/RTL translation: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Independent C/RTL translation proof incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cbmc", type=Path, required=True, help="CBMC executable (validated installation)"
    )
    parser.add_argument(
        "--work", type=Path, default=Path("/tmp/fpgaleet-c-independent-translation")
    )
    args = parser.parse_args()
    qualify(args.cbmc, args.work)


if __name__ == "__main__":
    main()
