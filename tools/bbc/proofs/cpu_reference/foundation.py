"""Rebuild the complete C CPU model and verify its translation in durable storage."""

import argparse
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pycparser
from pycparser import c_parser

from tools.bbc import cycle_reference
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    aig_c,
    frontend,
    independent_translation,
    prepared,
    prune,
    pruning_certificate,
    structural,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-foundation"
DEFAULT_CBMC = Path(shutil.which("cbmc") or "/usr/bin/cbmc")
PARTITIONS = [("hold", 0)] + [(kind, index) for kind in ("execute", "sync") for index in range(32)]


def partition_harness(source, kind, index):
    if (kind, index) not in PARTITIONS:
        raise ValueError("Unknown complete-source partition")
    if kind == "hold":
        restriction = "pins |= M6502_RW|M6502_RDY;"
    elif kind == "execute":
        restriction = (
            f"input.IR={index * 64}u|(input.IR&63u);\n"
            "pins &= ~M6502_SYNC;\n"
            "pins &= ~((pins&M6502_RW) ? M6502_RDY:0);"
        )
    else:
        restriction = (
            "pins |= M6502_SYNC;\n"
            f"pins=(pins&~UINT64_C(0xff0000))|(((pins>>16)&7u)|{index * 8}u)<<16;"
        )
    # This changes only the symbolic input domain. The full C function and all
    # AIG equations and output comparisons execute unchanged in every case.
    return replace_once(aig_c.harness(source), " bool valid=", restriction + "\n bool valid=")


def checked_header(source, kind, index):
    if (kind, index) not in PARTITIONS:
        raise ValueError("Unknown source dispatch domain")

    def keep(code):
        return (kind == "execute" and code // 64 == index) or (
            kind == "sync" and (code == 0 or (code % 8 == 0 and code // 8 // 8 == index))
        )

    pattern = r"(case \(0x([0-9A-Fa-f]{2})<<3\)\|([0-7]): )([^\n]+)"
    count = 0

    def replace(match):
        nonlocal count
        count += 1
        if not match[4].endswith("break;"):
            raise ValueError("Cannot remove a C case with fallthrough")
        code = int(match[2], 16) * 8 + int(match[3])
        return match[0] if keep(code) else ""

    result = re.sub(pattern, replace, source)
    if count != 2048:
        raise ValueError("Changed complete C case inventory")
    return replace_once(result, "default: _M6502_UNREACHABLE;", "default: assert(false);break;")


def partition_wrapper(kind, index):
    if (kind, index) not in PARTITIONS:
        raise ValueError("Unknown full-model partition")
    state = (
        f"{{(16'd{index * 64}|{{10'd0,state_in[245:240]}}),state_in[239:0]}}"
        if kind == "execute"
        else "state_in"
    )
    pins = {
        "hold": "(pins_in|64'h11000000)",
        "execute": "((pins_in&~64'h2000000)&~(pins_in[24] ? 64'h10000000:64'd0))",
        "sync": f"((pins_in|64'h2000000)&~64'hf80000)|64'd{index * 8 << 16}",
    }[kind]
    return f"""module M6502ReferenceNext(input [255:0] state_in,input [63:0] pins_in,output [255:0] state_out,output [63:0] pins_out,output trap);
Full original(.state_in({state}),.pins_in({pins}),.state_out(state_out),.pins_out(pins_out),.trap(trap));
endmodule
"""


def verify_partition_cover(source):
    codes = aig_c.microcodes(source)
    execute = [
        code for _, index in PARTITIONS if _ == "execute" for code in codes if code // 64 == index
    ]
    sync = [
        opcode
        for kind, index in PARTITIONS
        if kind == "sync"
        for opcode in range(index * 8, index * 8 + 8)
    ]
    if sorted(execute) != codes or len(execute) != len(set(execute)) or sync != list(range(256)):
        raise ValueError("Partition cover omits or duplicates a defined decoder input")
    return {
        "partitions": len(PARTITIONS),
        "defined_microsteps": len(codes),
        "sync_bytes": len(sync),
        "hold_all_state_and_pins_except_required_rw_rdy": True,
        "domain": "Hold reads admit arbitrary IR and SYNC. Otherwise SYNC admits every incoming opcode and arbitrary old IR. Remaining defined non-SYNC dispatches admit every implemented IR with arbitrary pins except read holds. The three regions cover the original defined-input domain; only SYNC/read-hold overlap is permitted.",
    }


def initial_observer(source, directory):
    fields = [name for name, _ in frontend.FIELDS]
    oracle = directory / "initial.c"
    expressions = ",".join("(unsigned long long)cpu." + name for name in fields)
    oracle.write_text(
        '#define CHIPS_IMPL\n#include "m6502.h"\n#include <stdio.h>\n'
        'int main(void){m6502_t cpu;m6502_desc_t desc={0};uint64_t pins=m6502_init(&cpu,&desc);printf("'
        + " ".join(["%llu"] * (len(fields) + 1))
        + '\\n",'
        + expressions
        + ",(unsigned long long)pins);return 0;}\n"
    )
    binary = directory / "initial"
    subprocess.run(
        ["gcc", "-O1", "-std=c11", str(oracle), "-o", str(binary)], check=True, timeout=60
    )
    observed = subprocess.check_output([str(binary)], text=True)
    (directory / "initial.log").write_text(observed)
    values = list(map(int, observed.split()))
    if len(values) != len(fields) + 1:
        raise ValueError("Incomplete independently executed initialization")
    initial = dict(zip(fields, values[:-1], strict=True))
    expected = {name: 0 for name in fields}
    expected.update(P=2, PINS=0x43000000, bcd_enabled=1)
    if initial != expected or values[-1] != expected["PINS"]:
        raise ValueError("Changed Model B native initialization")
    return initial, values[-1]


def sat(command, log, timeout=1200):
    with log.open("w") as output:
        trial = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    if trial.returncode or "SUCCESS!" not in log.read_text():
        raise ValueError("Complete-source preservation failed: " + str(log))


def qualify(cbmc=DEFAULT_CBMC, partition_timeout=600, jobs=1):
    if not 1 <= jobs <= len(PARTITIONS):
        raise ValueError("Partition workers must be between 1 and 65")
    started = time.monotonic()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    cbmc = cbmc.resolve(strict=True)
    source = cycle_reference.DIRECTORY / "m6502.h"
    upstream = source.with_name("m6502-original.h")
    if digest(upstream) != cycle_reference.locked_assets()[upstream.name][
        "sha256"
    ] or source.read_text() != cycle_reference.derive(upstream.read_text()):
        raise ValueError("C reference differs from its pinned maintained derivation")
    files = [
        Path(__file__),
        *[
            Path(module.__file__)
            for module in (
                aig_c,
                frontend,
                independent_translation,
                prepared,
                prune,
                pruning_certificate,
                structural,
                cycle_reference,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        source,
        upstream,
        cycle_reference.SOURCE / "lock.json",
        cbmc,
        *[Path(shutil.which(tool)) for tool in ("gcc", "yosys")],
        *sorted(Path(pycparser.__file__).parent.rglob("*.py")),
    ]
    before = hashes(files)
    cover = verify_partition_cover(source.read_text())
    expanded = subprocess.check_output(
        ["gcc", "-E", "-P", "-"], input=frontend.extract(source), text=True
    )
    full, model = DIRECTORY / "full.v", DIRECTORY / "source.v"
    full.write_text(structural.generate(c_parser.CParser().parse(expanded)))
    lowered, pruning = prune.prune(full.read_text())
    model.write_text(lowered)
    certificate = pruning_certificate.verify(full.read_text(), lowered)
    write_json(DIRECTORY / "pruning-certificate.json", certificate)
    shutil.copyfile(source, DIRECTORY / "m6502.h")
    initial, initial_pins = initial_observer(source, DIRECTORY)
    aag = DIRECTORY / "symbols.aag"
    command = f"read_verilog -sv {model}; prep -top M6502ReferenceNext; opt; techmap; opt; abc -g AND; opt_clean; write_aiger -ascii -symbols {aag}"
    cached = prepared.prepare(DIRECTORY, command, [model], [aag], timeout=1800)
    (DIRECTORY / "aig_eval.c").write_text(aig_c.generate(aag.read_text()))
    (DIRECTORY / "harness.c").write_text(aig_c.harness(source.read_text()))
    wrapper = DIRECTORY / "roundtrip-wrapper.v"
    wrapper.write_text(independent_translation.roundtrip_wrapper())
    # The acyclic identity certificate proves pruning. This separate SAT query
    # proves every bit of the gate export against the retained complete model.
    sat(
        f"read_verilog -sv {model}; prep -top M6502ReferenceNext; opt; techmap; opt; abc -g AND; opt_clean; rename M6502ReferenceNext Gold; read_aiger -module_name Gate {aag}; read_verilog {wrapper}; miter -equiv -flatten Gold AigRoundtrip Miter; prep -top Miter; opt -full; sat -verify -prove trigger 0",
        DIRECTORY / "roundtrip-proof.log",
        timeout=1800,
    )
    if independent_translation.native(DIRECTORY).returncode:
        raise ValueError("Fresh native C/gate comparison failed")
    print(
        "Complete model, pruning/export, native initialization and 100000 native transitions: PASS",
        flush=True,
    )
    faults = []
    for name, old, new in (
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
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        (case / "m6502.h").write_text(replace_once(source.read_text(), old, new))
        for filename in ("aig_eval.c", "harness.c"):
            shutil.copyfile(DIRECTORY / filename, case / filename)
        trial = independent_translation.native(case)
        detected = trial.returncode == 1 and "Failed " in (case / "native.log").read_text()
        faults.append({"name": name, "detected": detected})
    case = DIRECTORY / "wrong-exported-address"
    case.mkdir(exist_ok=True)
    lines = aag.read_text().splitlines()
    _, _, _, bindings = aig_c.parse(aag.read_text())
    index = 321 + bindings["o"][("pins_out", 0)]
    lines[index] = str(int(lines[index]) ^ 1)
    (case / "symbols.aag").write_text("\n".join(lines) + "\n")
    (case / "aig_eval.c").write_text(aig_c.generate((case / "symbols.aag").read_text()))
    shutil.copyfile(source, case / "m6502.h")
    shutil.copyfile(DIRECTORY / "harness.c", case / "harness.c")
    trial = independent_translation.native(case)
    faults.append(
        {
            "name": case.name,
            "detected": trial.returncode == 1
            and "Failed returned_pins" in (case / "native.log").read_text(),
        }
    )
    if not all(fault["detected"] for fault in faults):
        raise ValueError("Native comparison missed an actual source fault")

    def check_partition(partition):
        kind, index = partition
        name = f"{kind}-{index:02d}"
        case = DIRECTORY / "partitions" / name
        case.mkdir(parents=True, exist_ok=True)
        harness = case / "harness.c"
        harness.write_text(partition_harness(source.read_text(), kind, index))
        (case / "m6502.h").write_text(checked_header(source.read_text(), kind, index))
        prefix = case / "wrapper.v"
        prefix.write_text(partition_wrapper(kind, index))
        gates = case / "symbols.aag"
        adapter = case / "adapter.v"
        adapter.write_text(
            replace_once(
                independent_translation.roundtrip_wrapper(), "module AigRoundtrip(", "module Full("
            )
        )
        prepared.prepare(
            case,
            f"read_aiger -module_name Gate {aag}; read_verilog {adapter} {prefix}; prep -top M6502ReferenceNext -flatten; opt; techmap; opt; abc -g AND; opt_clean; write_aiger -ascii -symbols {gates}",
            [aag, adapter, prefix],
            [gates],
            timeout=600,
        )
        (case / "aig_eval.c").write_text(aig_c.generate(gates.read_text()))
        # Quote includes find the common exact C and AIG files independently of
        # the case directory. Each case performs a fresh CBMC invocation.
        command = [
            str(cbmc),
            str(harness),
            "-I",
            str(DIRECTORY),
            "--unwind",
            "1",
            "--unwinding-assertions",
            "--slice-formula",
            "--drop-unused-functions",
            "--reachability-slice-fb",
            "--symex-cache-dereferences",
        ]
        write_json(case / "command.json", command)
        wall = time.monotonic()
        trial = independent_translation.run(command, case / "proof.log", partition_timeout)
        accepted = trial.returncode == 0 and independent_translation.verified_cbmc(
            (case / "proof.log").read_text()
        )
        return {"name": name, "accepted": accepted, "seconds": time.monotonic() - wall}

    checks = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for check in pool.map(check_partition, PARTITIONS):
            checks.append(check)
            print(
                f"Complete C translation {check['name']}: {'PASS' if check['accepted'] else 'FAIL'} ({check['seconds']:.2f}s)",
                flush=True,
            )
            write_json(
                DIRECTORY / "progress.json",
                {"checks": checks, "cover": cover, "full_equivalence": False},
            )

    # Force a defined microstep outside execute-00 through its checked C
    # header. This must fail the excluded-dispatch assertion specifically.
    case = DIRECTORY / "excluded-dispatch-control"
    case.mkdir(exist_ok=True)
    base = DIRECTORY / "partitions/execute-00"
    for name in ("m6502.h", "aig_eval.c"):
        shutil.copyfile(base / name, case / name)
    (case / "harness.c").write_text(
        replace_once(
            (base / "harness.c").read_text(),
            " verify_step(input,pins);",
            " input.IR=64u;\n verify_step(input,pins);",
        )
    )
    command = [
        str(cbmc),
        str(case / "harness.c"),
        "--unwind",
        "1",
        "--unwinding-assertions",
        "--slice-formula",
        "--drop-unused-functions",
        "--reachability-slice-fb",
        "--symex-cache-dereferences",
    ]
    write_json(case / "command.json", command)
    trial = independent_translation.run(command, case / "proof.log", partition_timeout)
    log = (case / "proof.log").read_text()
    faults.append(
        {
            "name": case.name,
            "detected": trial.returncode == 10
            and "assertion false: FAILURE" in log
            and "VERIFICATION FAILED" in log,
        }
    )
    stable = before == hashes(files)
    passed = (
        stable
        and len(checks) == len(PARTITIONS)
        and all(case["accepted"] for case in checks)
        and all(fault["detected"] for fault in faults)
    )
    artifacts = [
        full,
        model,
        aag,
        wrapper,
        DIRECTORY / "prepared.json",
        DIRECTORY / "prepare.log",
        DIRECTORY / "roundtrip-proof.log",
        DIRECTORY / "m6502.h",
        DIRECTORY / "aig_eval.c",
        DIRECTORY / "harness.c",
        DIRECTORY / "native",
        DIRECTORY / "native-build.log",
        DIRECTORY / "native.log",
        DIRECTORY / "initial",
        DIRECTORY / "initial.c",
        DIRECTORY / "initial.log",
        DIRECTORY / "pruning-certificate.json",
    ]
    artifacts += [
        p
        for part in (DIRECTORY / "partitions", *[DIRECTORY / fault["name"] for fault in faults])
        for p in part.rglob("*")
        if p.is_file()
    ]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "partition_cover": cover,
            "checks": checks,
            "faults": faults,
            "default_native_state": initial,
            "default_native_pins": initial_pins,
            "pruning": pruning,
            "preparation_cache_reused": cached,
            "wall_seconds": time.monotonic() - started,
            "workers": jobs,
            "scope": "Complete maintained C tick versus regenerated full structural model. An independently checked acyclic equation certificate proves dead-wire pruning; SAT proves all 320 input/all 321 output bits preserved through AIG export. Each specialized gate model instantiates the exact complete exported gate network under its declared input constructor. Selected C cases and all surrounding source/helper code remain verbatim; a default assert(false) checks that no excluded case is reached. All 20 comparisons, safety/unwinding checks and all 65 exhaustive defined-input partitions must pass. Native C initialization and 100000 transitions freshly execute the complete C engine; actual C faults detect comparison failures. Durable inputs and evidence; no output masks.",
            "limits": "One defined C-typed transition and constructor profile. Does not compose instruction/reset histories with actual RTL, source JavaScript timing, device histories or whole-game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Complete source foundation incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cbmc", type=Path, default=DEFAULT_CBMC)
    parser.add_argument("--partition-timeout", type=int, default=600)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    qualify(args.cbmc, args.partition_timeout, args.jobs)
