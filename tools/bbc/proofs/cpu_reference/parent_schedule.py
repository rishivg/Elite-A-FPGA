"""Connect actual parent acceptance to independently executed Model B timing."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import event_composition
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-parent-schedule"


def slow_expression(slow_hex):
    """Losslessly compress the complete native truth table into address ranges."""
    if len(slow_hex) != 16384 or re.fullmatch("[0-9a-f]+", slow_hex) is None:
        raise ValueError("Incomplete source bus address table")
    bits, start, ranges = int(slow_hex, 16), None, []
    for address in range(65537):
        slow = address < 65536 and bool((bits >> address) & 1)
        if slow and start is None:
            start = address
        elif not slow and start is not None:
            ranges.append(f"(heldAddress>=16'h{start:04x} && heldAddress<=16'h{address - 1:04x})")
            start = None
    return " || ".join(ranges) or "1'b0"


def observed(source, top, slow_hex):
    slow = slow_expression(slow_hex)
    parent = bus_binding_proof.module(source, top)
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    for port, required in (("clk", "clk"), ("reset", "reset"), ("enable", "cpuEnable")):
        if cpu[port].strip() != required:
            raise ValueError("Changed parent CPU scheduling binding")
    phase = re.search(r"assign tickCpu = \(clock32 && \((\w+)\[3 : 0\] == 4'b1111\)\);", parent)
    if phase is None:
        raise ValueError("Changed parent slot phase")
    children = [
        instance
        for kind, instance in re.findall(r"^\s+(\w+)\s+(\w+)\s*\(\s*$", parent, re.M)
        if kind != "BbcAddressDecode"
    ]
    if "cpu" not in children:
        raise ValueError("Missing CPU request driver")
    fixture = f"""
// An arbitrary request held until actual acceptance replaces the CPU port.
// Stable returned data supplies the independently checked CPU hold premise.
(* anyconst *) reg [15:0] firstAddress;
(* anyseq *) reg [15:0] nextAddress;
reg [15:0] heldAddress=0;
assign cpu_address=heldAddress;
reg parity=0;
reg [1:0] remaining=0;
wire slowSource={slow};
wire [1:0] duration=slowSource ? (parity ? 2'd2:2'd3):2'd1;
wire [1:0] left=remaining==0 ? duration:remaining;
wire sourceAccept=tickCpu && left==1;
always @(posedge clk or posedge reset)
 if(reset)begin heldAddress<=firstAddress;parity<=0;remaining<=0;end
 else begin
  if(cpuEnable)heldAddress<=nextAddress;
  if(tickCpu)begin parity<=!parity;remaining<=sourceAccept ? 2'd0:left-2'd1;end
 end
initial assume(reset);
wire proofEquivalent=reset || (cpuEnable==sourceAccept &&
 parity=={phase[1]}[4] && remaining==(delayedSlow ? (parity ? 2'd2:2'd1):2'd0) &&
 (!delayedSlow || slowSource) && decode_io_slow==slowSource);
"""
    return (
        replace_once(source, parent, parent.removesuffix("endmodule") + fixture + "endmodule"),
        children,
    )


def prove_request_hold():
    """Address hold requires a held response, beyond state-only idle preservation."""
    cut = ROOT / "build/bbc/cpu-nmi-macro-transition-proof/actual-cut.json"
    case = DIRECTORY / "request-hold"
    case.mkdir(exist_ok=True)
    contract, graph, log = [case / name for name in ("contract.v", "graph.json", "proof.log")]
    contract.write_text("""module CpuRequestHold(input [167:0] state,
 input nmi,irq,nextNmi,nextIrq,nextEnable,input [7:0] data,output equivalent);
wire [167:0] following;
wire [15:0] beforeAddress,afterAddress;
NextActual before(.clk(1'b0),.reset(1'b0),.warm_reset(1'b0),.enable(1'b0),
 .state_in(state),.next_out(following),.nmi(nmi),.irq(irq),.read_data(data),.address(beforeAddress));
NextActual after(.clk(1'b0),.reset(1'b0),.warm_reset(1'b0),.enable(nextEnable),
 .state_in(following),.nmi(nextNmi),.irq(nextIrq),.read_data(data),.address(afterAddress));
assign equivalent=beforeAddress==afterAddress;
endmodule
""")
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {cut}; read_verilog {contract}; prep -top CpuRequestHold -flatten; check -assert; write_json {graph}; sat -prove equivalent 1 -verify -timeout 60",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
    if trial.returncode != 0 or "no model found: SUCCESS!" not in log.read_text():
        raise ValueError("Physical CPU address hold with stable response failed")
    return [contract, graph, log]


def guard_parent(graph, top):
    module = graph["modules"][top]
    assumptions = [cell for cell in module["cells"].values() if cell["type"] == "$assume"]
    initial = [cell for cell in module["cells"].values() if cell["type"] == "$initstate"]
    if (
        len(assumptions) != 1
        or len(initial) != 1
        or any(cell["type"] == "$check" for cell in module["cells"].values())
        or assumptions[0]["connections"]["A"] != module["ports"]["reset"]["bits"]
        or assumptions[0]["connections"]["EN"] != initial[0]["connections"]["Y"]
    ):
        raise ValueError("Parent scheduler assumptions exceed initial reset")
    for name, kind in (("firstAddress", "$anyconst"), ("nextAddress", "$anyseq")):
        wire = module["netnames"][name]
        if (
            len(wire["bits"]) != 16
            or "init" in wire["attributes"]
            or not any(
                cell["type"] == kind and cell["connections"]["Y"] == wire["bits"]
                for cell in module["cells"].values()
            )
        ):
            raise ValueError("Parent scheduler narrowed its address domain")


def qualify():
    current_machine.audit()
    premise = event_composition.DIRECTORY / "result.json"
    clock_paths = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    tree = event_composition.audit_tree([premise, *clock_paths])
    for (multiplier, path, _), receipt in zip(current_machine.parents(), clock_paths, strict=True):
        report = tree[receipt.resolve()]
        if (
            report.get("clock_multiplier") != multiplier
            or report["inputs"].get(str(path.relative_to(ROOT)))
            != hashes([path])[str(path.relative_to(ROOT))]
        ):
            raise ValueError("Parent slot clocks bind a different machine")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    native_script = Path(__file__).with_name("source_schedule.mjs")
    table_path = DIRECTORY / "source.json"
    files = [
        Path(__file__),
        native_script,
        reference / "src/6502.js",
        reference / "src/models.js",
        ROOT / "tools/bbc/reference_cycle.mjs",
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcAddressDecode.scala",
        *tree,
        current_machine.DIRECTORY / "generation.json",
        *[path for _, path, _ in current_machine.parents()],
        Path(bus_binding_proof.__file__),
        Path(current_machine.__file__),
        Path(event_composition.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("node")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    adapter = (ROOT / "tools/bbc/reference_cycle.mjs").read_text()
    duration = """remaining = cpu.is1MHzAccess(address, !(pins & 0x01000000))
          ? 2 + ((1 ^ cpu.currentCycles) & 1)
          : 1;"""
    if adapter.count(duration) != 1 or adapter.count("if (--remaining === 0)") != 1:
        raise ValueError("Cycle adapter changed its source access-duration rule")
    subprocess.run(
        ["node", str(native_script), str(reference), str(table_path)], check=True, timeout=30
    )
    table = json.loads(table_path.read_text())
    if table.get("passed") is not True or table.get("cases") != 262144:
        raise ValueError("Incomplete native source access timing cases")
    artifacts, checks = [table_path, *prove_request_hold()], []
    for multiplier, path, top in current_machine.parents():
        original = path.read_text()
        for name in ("positive", "missing-slow-wait", "wrong-slow-phase"):
            source = original
            if name == "missing-slow-wait":
                source = replace_once(source, "(tickVia && delayedSlow)", "tickVia")
            elif name == "wrong-slow-phase":
                source = replace_once(source, "phase_1 == 5'h0f", "phase_1 == 5'h1f")
            body, children = observed(source, top, table["slow_hex"])
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(exist_ok=True)
            model, graph, log, witness = [
                case / filename
                for filename in ("actual.v", "graph.json", "proof.log", "witness.json")
            ]
            model.write_text(body)
            witness.unlink(missing_ok=True)
            command = (
                f"read_verilog -formal {model}; delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; expose {top}/w:proofEquivalent; opt_clean -purge; flatten; async2sync; opt -full; check -assert; write_json {graph}; "
            )
            positive = name == "positive"
            horizon = (
                "-tempinduct -initsteps 2 -maxsteps 8" if positive else f"-seq {50 * multiplier}"
            )
            command += f"sat -set-assumes -set-init-zero {horizon} -prove proofEquivalent 1 -verify -timeout 90 -show proofEquivalent -show heldAddress -show cpuEnable -show sourceAccept -dump_json {witness}"
            with log.open("w") as output:
                trial = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                )
            output = log.read_text()
            accepted = (
                trial.returncode == 0 and "Induction step proven: SUCCESS!" in output
                if positive
                else trial.returncode != 0
                and "ERROR: Called with -verify and proof did fail!" in output
                and witness.is_file()
            )
            if graph.is_file():
                guard_parent(json.loads(graph.read_text()), top)
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([model, graph, log])
            if witness.is_file():
                artifacts.append(witness)
            print(
                f"Source/parent schedule {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Source/parent request scheduling proof failed")
    current_machine.audit()
    event_composition.audit_tree([premise, *clock_paths])
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs_unchanged": stable,
        "inputs": before,
        "artifacts": hashes(artifacts),
        "checks": checks,
        "native_cases": table["cases"],
        "stable_response_address_hold": True,
        "scope": "Initialized unbounded accepted-request timing of both actual parent schedulers against original Model B is1MHzAccess/polltimeAddr methods, independently executed for all 65536 addresses, both directions and both slot parities. Arbitrary successive requests hold until actual acceptance. A separate all-state actual-CPU query proves address hold with a stable read response despite arbitrary IRQ/NMI and following enable. The real address decoder, slot phase, master divider and slow-wait state remain executing; current 32/96 MHz slot-clock proofs are recursively audited. Only initial reset constrains formal inputs. Missing waits and shifted slow endpoints must fail at both rates.",
        "limits": "Relative slot epoch starts at reference cycle zero after declared shared reset. CPU request driver is replaced by a held-address interface. Applying this theorem to execution requires response settlement before the first slot of a request and retention through acceptance; that parent-memory/device obligation is explicit and not inferred from state-only idle preservation. Independent peripheral callback/NMI event generation, response values, absolute source epoch, warm-reset histories, opcode eligibility, video rendering and whole-game equivalence remain separate.",
        "universal_game_equivalence": False,
    }
    write_json(DIRECTORY / "result.json", report)
    if not stable:
        raise ValueError("Source/parent schedule inputs changed")
    return report


if __name__ == "__main__":
    qualify()
