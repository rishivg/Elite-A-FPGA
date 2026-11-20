"""Prove parent/source epochs and the actual FDC's sampled low acknowledgement gap."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, cpu_events, cpu_interrupt_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_order,
    fdc_epoch,
    parent_schedule,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/device-source-epoch"


def guard_protocol(reference, driver, adapter):
    for text, statements in (
        (
            reference,
            (
                "cpu.currentCycles += 16;",
                "cpu.scheduler.polltime(16);",
                'powerProfile === "shared-reset"',
            ),
        ),
        (
            driver,
            (
                "const uint64_t cycle = master / BBC_MASTER_MULTIPLIER;",
                "dut.reset = !restoring && cycle < 256;",
                "bus << (cycle + 1)",
            ),
        ),
        (adapter, ("cpu.polltime(1);", "slots++;", "if (--remaining === 0)", "engine.tick(pins)")),
    ):
        if any(text.count(statement) != 1 for statement in statements):
            raise ValueError("Changed maintained shared-reset/slot epoch protocol")
    if (
        not adapter.index("cpu.polltime(1);")
        < adapter.index("slots++;")
        < adapter.index("if (--remaining === 0)")
        < adapter.index("engine.tick(pins)")
    ):
        raise ValueError("Changed peripheral-before-access source slot ordering")


def epoch_model(source, top, multiplier, slow_hex):
    body, children = parent_schedule.observed(source, top, slow_hex)
    parent = bus_binding_proof.module(body, top)
    phase = re.search(r"assign tickCpu = \(clock32 && \((\w+)\[3 : 0\] == 4'b1111\)\);", parent)[1]
    if multiplier == 3:
        divider = re.search(r"assign clock32 = \((\w+) == 2'b10\);", parent)
        if divider is None:
            raise ValueError("Changed actual parent base divider")
        physical = f"({phase}[3:0]*6'd3)+{divider[1]}"
    else:
        physical = f"{phase}[3:0]"
    fixture = f"""
// Exact post-shared-reset source epoch. Labels count completed 32 MHz edges.
reg [5:0] masterSlot=0;
reg [52:0] sourceEpoch=53'd16;
reg [56:0] baseEpoch=57'd256;
reg [1:0] sinceAccepted=2;
wire expectedBase=masterSlot%{multiplier}=={multiplier - 1};
wire expectedSlot=masterSlot=={16 * multiplier - 1};
always @(posedge clk or posedge reset)
 if(reset)begin masterSlot<=0;sourceEpoch<=16;baseEpoch<=256;sinceAccepted<=2;end
 else begin
  masterSlot<=expectedSlot ? 0:masterSlot+1;
  if(expectedSlot)sourceEpoch<=sourceEpoch+1;
  if(expectedBase)baseEpoch<=baseEpoch+1;
  if(clock32)begin
   if(cpuEnable)sinceAccepted<=0;
   else if(sinceAccepted!=2)sinceAccepted<=sinceAccepted+1;
  end
 end
wire proofEpoch=reset || (clock32==expectedBase && tickCpu==expectedSlot &&
 masterSlot=={physical} && sourceEpoch[0]=={phase}[4] &&
 baseEpoch=={{sourceEpoch,4'b0}}+{phase}[3:0] &&
 (!tickCpu || baseEpoch+1=={{sourceEpoch,4'b0}}+16));
wire proofBusGap=reset || (!cpuEnable || sinceAccepted==2);
wire proofGapInvariant=(sinceAccepted!=0 || {phase}[3:0]==0) &&
 (sinceAccepted!=1 || {phase}[3:0]==1) && sinceAccepted<=2;
wire proofJoined=proofEquivalent && proofEpoch && proofBusGap && (reset || proofGapInvariant);
"""
    return replace_once(
        body, parent, parent.removesuffix("endmodule") + fixture + "endmodule"
    ), children


def run_sat(case, command, property_name, positive, show="", horizon=6):
    log, witness = case / "proof.log", case / "witness.json"
    witness.unlink(missing_ok=True)
    mode = "-tempinduct -initsteps 2 -maxsteps 10" if positive else f"-seq {horizon}"
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                command
                + f"; sat -set-assumes -set-init-zero {mode} -prove {property_name} 1 -verify -timeout 90 -show {property_name} {show} -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    text = log.read_text()
    accepted = (
        trial.returncode == 0 and "Induction step proven: SUCCESS!" in text
        if positive
        else trial.returncode != 0
        and "ERROR: Called with -verify and proof did fail!" in text
        and witness.is_file()
        and any(
            s["name"] == property_name and "0" in s.get("wave", "")
            for s in json.loads(witness.read_text())["signal"]
        )
    )
    if not accepted:
        raise ValueError("Device epoch/spacing SAT check incomplete: " + str(case))
    return [log, *([witness] if witness.is_file() else [])]


def epoch_proof(multiplier, path, top, slow_hex):
    artifacts, checks = [], []
    for name in ("positive", "wrong-source-origin", "shifted-parent-slot"):
        case = DIRECTORY / f"epoch-{name}{multiplier}"
        case.mkdir(parents=True, exist_ok=True)
        source, children = epoch_model(path.read_text(), top, multiplier, slow_hex)
        if name == "wrong-source-origin":
            source = replace_once(source, "sourceEpoch<=16;", "sourceEpoch<=15;")
        elif name == "shifted-parent-slot":
            tick = re.search(r"  assign tickCpu = [^;]+;", source)[0]
            source = replace_once(source, tick, tick.replace("4'b1111", "4'b1110"))
        model, graph = case / "actual.v", case / "graph.json"
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(path.parent / m[1]) + '"',
                source,
            )
        )
        command = f"read_verilog -formal {model}; delete " + " ".join(
            f"{top}/c:{child}" for child in children
        )
        command += f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; expose {top}/w:proofJoined; opt_clean -purge; flatten; async2sync; opt -full; check -assert; write_json {graph}"
        result = run_sat(
            case,
            command,
            "proofJoined",
            name == "positive",
            "-show proofEpoch -show proofBusGap -show masterSlot -show sourceEpoch",
            horizon=50 * multiplier,
        )
        parent_schedule.guard_parent(json.loads(graph.read_text()), top)
        artifacts.extend([model, graph, *result])
        checks.append({"clock_multiplier": multiplier, "name": name, "accepted": True})
        print(f"Device epoch {multiplier} {name}: PASS", flush=True)
    return checks, artifacts


def gap_proof(multiplier, path, top):
    controller, extra = callback_order.fdc_instance(path.read_text(), top)
    base = extra[0] if extra else "1'b1"
    fixture = f"""
reg proofAcknowledged=0;
wire proofAck=(io_read && io_address==1) || ((io_read || io_write) && io_address[2]);
always @(posedge clk or posedge reset)
 if(reset)proofAcknowledged<=0;
 else proofAcknowledged<={base} && proofAck && io_nmi;
wire proofLow=reset || !proofAcknowledged || !io_nmi;
wire proofEquivalent=proofLow && (reset || !proofAcknowledged || nmiGap);
initial assume(reset);
"""
    artifacts, checks = [], []
    for name in ("positive", "missing-gap"):
        case = DIRECTORY / f"gap-{name}{multiplier}"
        case.mkdir(parents=True, exist_ok=True)
        source = replace_once(controller, "endmodule", fixture + "endmodule")
        if name != "positive":
            source = replace_once(source, " && (! nmiGap)", "")
        model, graph = case / "actual.v", case / "graph.json"
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(path.parent / m[1]) + '"',
                source,
            )
        )
        # The full-state proofs retain the architectural mode byte. Its upstream
        # state is outside this downstream, freely varying event-stage relation.
        command = f"read_verilog -formal -D SYNTHESIS {model}; hierarchy -top Intel8271; proc; opt_expr -mux_bool; delete -output Intel8271/o:*; cutpoint Intel8271/w:eventNmi Intel8271/w:commandNmi; expose Intel8271/w:proofEquivalent Intel8271/w:proofLow; setattr -unset keep Intel8271/w:mode; opt_clean -purge; async2sync; opt -full; check -assert; write_json {graph}"
        result = run_sat(
            case,
            command,
            "proofEquivalent" if name == "positive" else "proofLow",
            name == "positive",
            "-show proofAcknowledged -show nmiGap -show io_nmi",
        )
        guard_gap(json.loads(graph.read_text()))
        artifacts.extend([model, graph, *result])
        checks.append({"clock_multiplier": multiplier, "name": name, "accepted": True})
        print(f"FDC sampled gap {multiplier} {name}: PASS", flush=True)
    return checks, artifacts


def guard_gap(graph):
    module = graph["modules"]["Intel8271"]
    assumptions = [cell for cell in module["cells"].values() if cell["type"] == "$assume"]
    initial = [cell for cell in module["cells"].values() if cell["type"] == "$initstate"]
    if (
        len(assumptions) != 1
        or len(initial) != 1
        or assumptions[0]["connections"]["A"] != module["ports"]["reset"]["bits"]
        or assumptions[0]["connections"]["EN"] != initial[0]["connections"]["Y"]
        or any(cell["type"] == "$check" for cell in module["cells"].values())
    ):
        raise ValueError("FDC gap assumptions exceed initial reset")
    state = []
    for cell in module["cells"].values():
        if "ff" in cell["type"].lower():
            if (
                cell["type"] not in {"$dff", "$sdff"}
                or cell["connections"]["CLK"] != module["ports"]["clk"]["bits"]
                or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
            ):
                raise ValueError("Changed FDC gap state or clock")
            state.extend(cell["connections"]["Q"])
    if len(state) != 2 or len(set(state)) != 2:
        raise ValueError("FDC gap must retain actual gap and acknowledgement monitor state")
    for name in ("eventNmi", "commandNmi"):
        bits = module["netnames"][name]["bits"]
        if (
            len(bits) != 1
            or sum(
                cell["type"] == "$anyseq" and cell["connections"]["Y"] == bits
                for cell in module["cells"].values()
            )
            != 1
        ):
            raise ValueError("FDC gap narrowed callback/command inputs")


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for (multiplier, path, _), clock_path in zip(current_machine.parents(), clocks, strict=True):
        clock = memory_composition.sealed(clock_path)
        if (
            clock.get("clock_multiplier") != multiplier
            or clock["inputs"].get(str(path.relative_to(ROOT)))
            != hashes([path])[str(path.relative_to(ROOT))]
        ):
            raise ValueError("Device epoch clock evidence binds a different parent")
    files = [
        Path(__file__),
        Path(__file__).with_name("epoch_source.mjs"),
        Path(fdc_epoch.__file__),
        Path(fdc_epoch.__file__).with_suffix(".mjs"),
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "sim/native/bbc.cpp",
        ROOT / "tools/bbc/reference.mjs",
        ROOT / "tools/bbc/reference_cycle.mjs",
        ROOT / "tools/bbc/reference_events.mjs",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcAddressDecode.scala",
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "build/bbc/ArletCpuEnabled.v",
        *cpu_events.files(),
        *[
            Path(module.__file__)
            for module in (
                current_machine,
                memory_composition,
                bus_binding_proof,
                cpu_interrupt_proof,
                callback_order,
                parent_schedule,
            )
        ],
        Path(parent_schedule.__file__).with_name("source_schedule.mjs"),
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        current_machine.DIRECTORY / "generation.json",
        *clocks,
        *[path for _, path, _ in current_machine.parents()],
        *[blob for _, path, _ in current_machine.parents() for blob in path.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[reference / "public/roms" / name for name in ("os.rom", "BASIC.ROM", "b/DFS-1.2.rom")],
        *[Path(shutil.which(tool)) for tool in ("yosys", "node", "verilator", "c++")],
    ]
    before = hashes(files)
    guard_protocol(
        (ROOT / "tools/bbc/reference.mjs").read_text(),
        (ROOT / "sim/native/bbc.cpp").read_text(),
        (ROOT / "tools/bbc/reference_cycle.mjs").read_text(),
    )
    table = DIRECTORY / "source.json"
    subprocess.run(
        [
            "node",
            str(Path(parent_schedule.__file__).with_name("source_schedule.mjs")),
            str(reference),
            str(table),
        ],
        check=True,
        timeout=30,
    )
    source = json.loads(table.read_text())
    if not source.get("passed") or source.get("cases") != 262144:
        raise ValueError("Incomplete original slot timing table")
    checks, artifacts = [], [table]
    subprocess.run(
        [
            "node",
            str(Path(__file__).with_name("epoch_source.mjs")),
            str(reference),
            str(ROOT / "tools/bbc/reference.mjs"),
            str(ROOT / "tools/bbc/reference_events.mjs"),
            str(DIRECTORY),
        ],
        check=True,
        timeout=60,
    )
    origin = json.loads((DIRECTORY / "source-origin.json").read_text())
    if (
        not origin.get("passed")
        or origin.get("shared_reset_epoch") != 16
        or origin.get("bus_requests") != 64
    ):
        raise ValueError("Original shared-reset/adapter epoch execution incomplete")
    artifacts.extend([DIRECTORY / "source-origin.json", DIRECTORY / "engine-proxy.cjs"])
    for multiplier, path, top in current_machine.parents():
        for proof in (
            lambda: epoch_proof(multiplier, path, top, source["slow_hex"]),
            lambda: gap_proof(multiplier, path, top),
        ):
            c, a = proof()
            checks.extend(c)
            artifacts.extend(a)
    c, a = fdc_epoch.run_native()
    checks.extend(c)
    artifacts.extend(a)
    current_machine.audit()
    for path in clocks:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "source_address_cases": source["cases"],
            "source_command_cases": 7,
            "source_reset_slots": 16,
            "original_epoch_requests": origin["bus_requests"],
            "native_reset_base_clocks": 256,
            "scope": "Fresh initialized unbounded induction of actual 32/96 MHz parent slots and accepted requests against the original address-duration table, with source epoch 16 and native base-clock label 256 after the maintained shared reset. Exact epoch recurrence and label equality are proved using 53-bit source/57-bit native counters; absolute interpretation applies throughout the original safe-integer epoch domain. The actual parent admits no consecutive or next-base accepted access. Separate induction retains the actual FDC gap flip-flop, abstracts callback/command stages freely, and proves a high acknowledged pin is low on the following physical sample at either rate. Seven original public cold command histories use the actual parent divider/decoder/strobes/FDC and an actual held CPU detector, checking every master sample and callback counts before/on each source deadline. Missing gap, wrong source origin and shifted parent slots must fail in their stated domains.",
            "limits": "Epoch alignment is for the declared cold shared-reset protocol, with arbitrary held requests; CPU instruction execution is replaced in the device probe. Actual callback values and complete source/device event histories are not established by the clock recurrence. Native byte rises are qualified within their containing 2 MHz source slots, not claimed at identical sub-slot master edges. Arbitrary resets, abort/retry/storage histories, IRQ generation, CPU macro-state correspondence, video eligibility and whole-game/board equivalence remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Device epoch/spacing proof inputs changed")
    return checks, artifacts


if __name__ == "__main__":
    qualify()
