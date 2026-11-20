"""Join callback windows to complete source/physical CPU transition relations."""

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import (
    callback_order,
    callback_summary,
    event_composition,
    explicit_events,
    foundation,
    frontend,
    nmi_sampling,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-callback-composition"
MACRO = ROOT / "build/bbc/cpu-nmi-macro-transition-proof"
FUNCTIONAL = (
    "address",
    "write_data",
    "write_enable",
    "instruction",
    "sync",
    "pc",
    "accumulator",
    "index_x",
    "index_y",
    "stack_pointer",
    "flags",
)


def guard_inputs(graph, top, inputs, sequential=False):
    body = graph["modules"][top]
    if any(cell["type"] in {"$assume", "$check"} for cell in body["cells"].values()):
        raise ValueError("Callback/CPU relation acquired an assumption")
    for name, width in inputs.items():
        port = body["ports"].get(name, {})
        if port.get("direction") != "input" or len(port["bits"]) != width:
            raise ValueError("Narrowed callback/CPU input: " + name)
    if not sequential and any(
        any(token in cell["type"].lower() for token in ("ff", "latch", "mem"))
        for cell in body["cells"].values()
    ):
        raise ValueError("Transition relation retained hidden state")


def prove(case, command, prop, positive, induction=False, horizon=12, show=""):
    log, witness = case / "proof.log", case / "witness.json"
    witness.unlink(missing_ok=True)
    mode = (
        "-set-init-zero -tempinduct -initsteps 2 -maxsteps 12"
        if induction
        else (f"-set-init-zero -seq {horizon}" if horizon else "")
    )
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                command
                + f"; sat {mode} -prove {prop} 1 -verify -timeout 120 -show {prop} {show} -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    text = log.read_text()
    success = "Induction step proven: SUCCESS!" if induction else "no model found: SUCCESS!"
    accepted = (
        trial.returncode == 0 and success in text
        if positive
        else trial.returncode != 0
        and "ERROR: Called with -verify and proof did fail!" in text
        and witness.is_file()
        and any(
            signal["name"] == prop and "0" in signal.get("wave", "")
            for signal in json.loads(witness.read_text())["signal"]
        )
    )
    print(f"Callback CPU {case.name}: " + ("PASS" if accepted else "FAIL"), flush=True)
    if not accepted:
        raise ValueError("Callback/CPU proof incomplete: " + str(case))
    return [log, *([witness] if witness.is_file() else [])]


def normalization_indices(graph):
    body = graph["modules"]["NextMacro"]
    bits = body["ports"]["state_in"]["bits"]
    if len(bits) != 168 or len(set(bits)) != 168 or len(body["ports"]["next_out"]["bits"]) != 168:
        raise ValueError("Incomplete actual CPU state inventory")
    fields = {}
    for name, width in (("core.NMI_1", 1), ("reset_dummy", 2)):
        field = body["netnames"][name]["bits"]
        if len(field) != width or not set(field).issubset(bits):
            raise ValueError("Changed physical CPU normalization field")
        fields[name] = [bits.index(bit) for bit in field]
    if set(fields["core.NMI_1"]) & set(fields["reset_dummy"]):
        raise ValueError("CPU pin normalization overlaps startup state")
    return fields


def projection_contract(prefix, source):
    """Bind the new constructor to the executing all-instruction recipe."""
    summary = event_composition.summary_contract(prefix)
    projection = summary[
        summary.index("  wire [31:0] eventPipeline=") : summary.index("assign equivalent=")
    ]
    statements = []
    for name in ("withEvent", "beforeA"):
        matches = re.findall(r"wire\[255:0\] " + name + r"=[^;]+;", source)
        if len(matches) != 1:
            raise ValueError("Changed CPU event constructor: " + name)
        statements.append(matches[0])
    return (
        """module CallbackProjection(input[255:0] state,input event,pinA,output equivalent);
wire[255:0] initialState=state;
wire offEvent=event,freshRise=0,acceptedNmi=pinA;
"""
        + "\n".join(statements)
        + "\n"
        + projection
        + "assign equivalent=beforeA==eventState;\nendmodule\n"
    )


def projection_proof(prefix):
    source = Path(__file__).with_name("callback_cpu_source.v").read_text()
    body = projection_contract(prefix, source)
    artifacts = []
    for name, text in (
        ("positive", body),
        ("wrong-marker", replace_once(body, "256'd1<<56", "256'd1<<55")),
    ):
        case = DIRECTORY / ("projection-" + name)
        case.mkdir(parents=True, exist_ok=True)
        contract, graph = case / "contract.v", case / "graph.json"
        contract.write_text(text)
        result = prove(
            case,
            f"read_verilog {contract}; prep -top CallbackProjection; opt; check -assert; write_json {graph}",
            "equivalent",
            name == "positive",
            horizon=0,
            show="-show-inputs",
        )
        guard_inputs(
            json.loads(graph.read_text()),
            "CallbackProjection",
            {"state": 256, "event": 1, "pinA": 1},
        )
        artifacts.extend([contract, graph, *result])
    return artifacts


def rtl_contract(graph):
    body = graph["modules"]["NextMacro"]
    fields = normalization_indices(graph)
    previous = fields["core.NMI_1"][0]
    dummy = "{" + ",".join(f"state[{bit}]" for bit in reversed(fields["reset_dummy"])) + "}"
    declarations, ports, comparisons = [], [[], []], []
    for name in FUNCTIONAL:
        width = len(body["ports"][name]["bits"])
        declarations.append(f"wire[{width - 1}:0] a_{name},b_{name};")
        for i, prefix in enumerate(("a", "b")):
            ports[i].append(f".{name}({prefix}_{name})")
        comparisons.append(f"a_{name}==b_{name}")
    return f"""module RtlPinNormalization(input[167:0] state,input previousA,previousB,pinA,pinB,
input offA,riseA,offB,riseB,irq,input[7:0]data,output equivalent);
wire[167:0] beforeA=(state & ~(168'd1<<{previous})) | ({{167'd0,previousA}}<<{previous});
wire[167:0] beforeB=(state & ~(168'd1<<{previous})) | ({{167'd0,previousB}}<<{previous});
wire[167:0] nextA,nextB;
{"".join(declarations)}
NextMacro left(.state_in(beforeA),.next_out(nextA),.clk(1'b0),.reset(1'b0),.enable(1'b1),
 .warm_reset(1'b0),.nmi(pinA),.irq(irq),.read_data(data),.offEvt(offA),.riseEvt(riseA),{",".join(ports[0])});
NextMacro right(.state_in(beforeB),.next_out(nextB),.clk(1'b0),.reset(1'b0),.enable(1'b1),
 .warm_reset(1'b0),.nmi(pinB),.irq(irq),.read_data(data),.offEvt(offB),.riseEvt(riseB),{",".join(ports[1])});
assign equivalent=({dummy}!=0) || ((offA||riseA)!=(offB||riseB)) ||
 ((((nextA^nextB)&~(168'd1<<{previous}))==0) && nextA[{previous}]==pinA && nextB[{previous}]==pinB &&
 {" && ".join(comparisons)});
endmodule
"""


def normalization():
    artifacts = []
    graph = json.loads((MACRO / "macro-cut.json").read_text())
    actual = json.loads((MACRO / "actual-cut.json").read_text())
    actual["modules"]["NextMacro"] = actual["modules"].pop("NextActual")
    if normalization_indices(graph) != normalization_indices(actual):
        raise ValueError("Macro/actual CPU use different named register positions")
    rtl = rtl_contract(graph)
    source = Path(__file__).with_name("callback_cpu_source.v").read_text()
    for family, top, model, variants in (
        (
            "rtl",
            "RtlPinNormalization",
            MACRO / "macro-cut.json",
            [
                ("positive", rtl),
                ("lost-rise", replace_once(rtl, ".riseEvt(riseB)", ".riseEvt(1'b0)")),
                ("level-as-event", replace_once(rtl, ".riseEvt(riseB)", ".riseEvt(pinB)")),
            ],
        ),
        (
            "source",
            "SourcePinNormalization",
            foundation.DIRECTORY / "source.v",
            [
                ("positive", source),
                ("lost-event", replace_once(source, "beforeB=(withEvent &", "beforeB=(state &")),
                (
                    "level-as-event",
                    replace_once(
                        source,
                        "beforeB=(withEvent &",
                        "beforeB=((state | (pinB ? (256'd1<<56):256'd0)) &",
                    ),
                ),
            ],
        ),
    ):
        for name, text in variants:
            case = DIRECTORY / f"{family}-{name}"
            case.mkdir(parents=True, exist_ok=True)
            contract, mapped = case / "contract.v", case / "graph.json"
            contract.write_text(text)
            read = f"read_json {model}" if family == "rtl" else f"read_verilog -formal -sv {model}"
            optimize = (
                "opt" if family == "rtl" else "opt; techmap; opt -fast; abc -g AND; opt_clean"
            )
            prepared.prepare(
                case,
                f"{read}; read_verilog -formal {contract}; prep -top {top} -flatten; check -assert; {optimize}; write_json {mapped}",
                [model, contract],
                [mapped],
            )
            inputs = (
                {
                    "state": 168,
                    "data": 8,
                    **{
                        name: 1
                        for name in (
                            "previousA",
                            "previousB",
                            "pinA",
                            "pinB",
                            "offA",
                            "riseA",
                            "offB",
                            "riseB",
                            "irq",
                        )
                    },
                }
                if family == "rtl"
                else {"state": 256, "pins": 64, "pinA": 1, "pinB": 1, "event": 1}
            )
            guard_inputs(json.loads(mapped.read_text()), top, inputs)
            prop = "observation" if family == "source" and name != "positive" else "equivalent"
            logs = prove(
                case,
                f"read_json {mapped}",
                prop,
                name == "positive",
                horizon=0,
                show="-show-inputs",
            )
            artifacts.extend(
                [contract, mapped, case / "prepared.json", case / "prepare.log", *logs]
            )
    return artifacts


def histories():
    body = Path(__file__).with_name("callback_cpu_history.v").read_text()
    sampler = Path(nmi_sampling.__file__).with_name("nmi_sample.v")
    artifacts = []
    for multiplier in (1, 3):
        text = replace_once(body, "CALLBACK_BASE", "1'b1" if multiplier == 1 else "divider==2")
        variants = [
            ("positive", text),
            (
                "lost-accepted-edge",
                replace_once(text, "postSample[56]==(pending || rise)", "postSample[56]==pending"),
            ),
            ("lost-masked-callback", replace_once(text, "level<=post;", "level<=post && !gap;")),
            (
                "lost-abort-event",
                replace_once(text, "wire delivered=retrigger;", "wire delivered=0;"),
            ),
            (
                "lost-accepted-abort",
                replace_once(
                    text, "wire delivered=retrigger;", "wire delivered=retrigger && !accepted;"
                ),
            ),
        ]
        variants.extend(callback_summary.reset_faults(text, "callbackHeld"))
        if multiplier == 3:
            variants.append(
                (
                    "unheld-callback",
                    replace_once(
                        replace_once(
                            text,
                            "   level<=post;gap<=ack && pin;callbackHeld<=callbackInput;",
                            "   level<=post;gap<=ack && pin;",
                        ),
                        "  eligible<=edgeEligible;",
                        "  eligible<=edgeEligible;callbackHeld<=callbackInput;",
                    ),
                )
            )
        for name, model in variants:
            case = DIRECTORY / f"history-{name}{multiplier}"
            case.mkdir(parents=True, exist_ok=True)
            contract, graph = case / "contract.v", case / "graph.json"
            contract.write_text(model)
            prepared.prepare(
                case,
                f"read_verilog -formal -sv {sampler} {contract}; prep -top PhysicalCallbackHistory -flatten; opt; check -assert; write_json {graph}",
                [sampler, contract],
                [graph],
            )
            guard_history(json.loads(graph.read_text()))
            logs = prove(
                case,
                f"read_json {graph}",
                "equivalent" if name == "positive" else "observation",
                name == "positive",
                induction=name == "positive",
                horizon=18,
                show="-show edgeEligible -show accepted -show callbackHeld -show gap -show resetAccess -show resetPending -show pending -show rise",
            )
            artifacts.extend([contract, graph, case / "prepared.json", case / "prepare.log", *logs])
    return artifacts


def guard_history(graph):
    body = graph["modules"]["PhysicalCallbackHistory"]
    if any(cell["type"] in {"$assume", "$check"} for cell in body["cells"].values()):
        raise ValueError("Physical callback history acquired an assumption")
    for name, width, kind in (
        ("initialState", 256, "$anyconst"),
        ("initialCallback", 1, "$anyconst"),
        *[
            (n, 1, "$anyseq")
            for n in (
                "callbackInput",
                "acceptInput",
                "acknowledge",
                "commandEvent",
                "abortEvent",
                "resetRequest",
            )
        ],
    ):
        wire = body["netnames"][name]
        if (
            len(wire["bits"]) != width
            or "init" in wire["attributes"]
            or sum(
                cell["type"] == kind and cell["connections"]["Y"] == wire["bits"]
                for cell in body["cells"].values()
            )
            != 1
        ):
            raise ValueError("Narrowed physical callback history input: " + name)


def cut_fdc(graph):
    body = graph["modules"]["Intel8271"]
    if body.get("processes") or any(
        cell["type"] in {"$assume", "$check"} for cell in body["cells"].values()
    ):
        raise ValueError("FDC certificate has hidden processes or assumptions")
    state, next_bits = [], []
    for name, cell in list(body["cells"].items()):
        if cell["type"] not in {"$dff", "$adff"}:
            if any(token in cell["type"].lower() for token in ("ff", "latch", "mem")):
                raise ValueError("Unknown FDC storage in callback certificate")
            continue
        ports = cell["connections"]
        if (
            ports["CLK"] != body["ports"]["clk"]["bits"]
            or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed FDC state clock")
        if cell["type"] == "$adff" and (
            ports["ARST"] != body["ports"]["reset"]["bits"]
            or int(cell["parameters"]["ARST_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed FDC asynchronous reset")
        if len(ports["Q"]) != len(ports["D"]):
            raise ValueError("Changed FDC state width")
        state.extend(ports["Q"])
        next_bits.extend(ports["D"])
        del body["cells"][name]
    if not state or len(set(state)) != len(state):
        raise ValueError("Incomplete FDC state inventory")
    for wire in body["netnames"].values():
        wire["attributes"].pop("init", None)
    for field in ("nmiLevel", "nmiGap"):
        if not set(body["netnames"][field]["bits"]).issubset(state):
            raise ValueError("Missing FDC interrupt storage")
    body["ports"]["proofState"] = {"direction": "input", "bits": state}
    body["ports"]["proofNext"] = {"direction": "output", "bits": next_bits}
    for name, field in (
        ("proofEvent", "eventNmi"),
        ("proofCommand", "commandNmi"),
        ("proofAbort", "abortNmi"),
    ):
        body["ports"][name] = {"direction": "output", "bits": body["netnames"][field]["bits"]}
    body["attributes"]["top"] = "0"
    return {"modules": {"FdcStateNext": body}}


def support(body, bits):
    boundaries = {
        bit: name
        for name, port in body["ports"].items()
        if port["direction"] == "input"
        for bit in port["bits"]
    }
    drivers = {}
    for name, cell in body["cells"].items():
        for port, direction in cell["port_directions"].items():
            if direction != "output":
                continue
            for bit in cell["connections"][port]:
                if not isinstance(bit, int):
                    continue
                if bit in drivers or bit in boundaries:
                    raise ValueError("Multiple support drivers")
                drivers[bit] = (name, cell)
    active, cache = set(), {}

    def visit(bit):
        if isinstance(bit, str):
            if bit not in {"0", "1"}:
                raise ValueError("Unknown constant in callback cone")
            return set()
        if bit in boundaries:
            return {boundaries[bit]}
        if bit in cache:
            return cache[bit]
        if bit in active or bit not in drivers:
            raise ValueError("Cyclic or undriven callback cone")
        active.add(bit)
        _, cell = drivers[bit]
        if cell["type"] not in {
            "$add",
            "$and",
            "$eq",
            "$le",
            "$logic_and",
            "$logic_not",
            "$logic_or",
            "$lt",
            "$mul",
            "$mux",
            "$ne",
            "$neg",
            "$not",
            "$or",
            "$pmux",
            "$reduce_and",
            "$reduce_bool",
            "$reduce_or",
            "$shift",
            "$shiftx",
            "$sshl",
            "$sub",
            "$xor",
        }:
            raise ValueError("Unknown or nondeterministic callback primitive")
        found = set()
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                for value in cell["connections"][port]:
                    found.update(visit(value))
        active.remove(bit)
        cache[bit] = found
        return found

    found = set()
    for bit in bits:
        found.update(visit(bit))
    return sorted(found)


def callback_support(body):
    deps = support(body, body["ports"]["proofEvent"]["bits"] + body["ports"]["proofAbort"]["bits"])
    address_deps = support(body, body["ports"]["io_storageAddress"]["bits"])
    if not set(deps).issubset({"proofState", "io_storageData"}) or address_deps != ["proofState"]:
        raise ValueError("FDC callbacks vary on unsupported between-base inputs")
    return deps, address_deps


def lift_support_unknowns(body):
    """Make each internal X adversarial while preserving all real input bits."""
    lifted = copy.deepcopy(body)
    if any(
        not isinstance(bit, int)
        for port in lifted["ports"].values()
        if port["direction"] == "input"
        for bit in port["bits"]
    ):
        raise ValueError("Unknown physical callback input")
    bits = [bit for port in lifted["ports"].values() for bit in port["bits"]]
    bits.extend(
        bit
        for cell in lifted["cells"].values()
        for port in cell["connections"].values()
        for bit in port
    )
    bits.extend(bit for net in lifted["netnames"].values() for bit in net["bits"])
    fresh = max(bit for bit in bits if isinstance(bit, int)) + 1
    unknowns = []

    def lifted_bits(values):
        nonlocal fresh
        result = []
        for bit in values:
            if bit == "x":
                result.append(fresh)
                unknowns.append(fresh)
                fresh += 1
            elif isinstance(bit, int) or bit in {"0", "1"}:
                result.append(bit)
            else:
                raise ValueError("Unsupported callback unknown constant")
        return result

    for cell in lifted["cells"].values():
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                cell["connections"][port] = lifted_bits(cell["connections"][port])
    for port in lifted["ports"].values():
        if port["direction"] == "output":
            port["bits"] = lifted_bits(port["bits"])
    if unknowns:
        lifted["ports"]["proofUndefined"] = {"direction": "input", "bits": unknowns}
    return lifted, len(unknowns)


def normalized_support(case, cut, body):
    """Prove callback/address outputs before using the normalized support cone."""
    directory = case / "support"
    directory.mkdir(exist_ok=True)
    normalized = directory / "normalized.json"
    prepared.prepare(
        directory,
        f"read_json {cut}; opt -full; check -assert; write_json {normalized}",
        [cut],
        [normalized],
    )
    actual = json.loads(normalized.read_text())["modules"]["FdcStateNext"]
    deps, addresses = callback_support(actual)
    original, unknowns = lift_support_unknowns(body)
    selected = ("proofEvent", "proofAbort", "io_storageAddress")
    actual = copy.deepcopy(actual)
    for module in (original, actual):
        for name in list(module["ports"]):
            if module["ports"][name]["direction"] == "output" and name not in selected:
                del module["ports"][name]
                module["netnames"].pop(name, None)
        module["attributes"]["top"] = "0"
    relation = directory / "relation.json"
    write_json(relation, {"modules": {"SupportOriginal": original, "SupportNormalized": actual}})
    inputs, source_ports, actual_ports = [], [], []
    for name, port in original["ports"].items():
        if port["direction"] != "input":
            continue
        inputs.append(f"input[{len(port['bits']) - 1}:0]{name}")
        source_ports.append(f".{name}({name})")
        if name != "proofUndefined":
            actual_ports.append(f".{name}({name})")
    wires, comparisons = [], []
    for name in selected:
        width = len(original["ports"][name]["bits"])
        if len(actual["ports"][name]["bits"]) != width:
            raise ValueError("Normalized callback output changed width")
        wires.append(f"wire[{width - 1}:0]{name}A,{name}B;")
        source_ports.append(f".{name}({name}A)")
        actual_ports.append(f".{name}({name}B)")
        comparisons.append(f"{name}A=={name}B")
    wrapper = directory / "contract.v"
    wrapper.write_text(
        f"module FdcSupportNormalization({','.join(inputs)},output equivalent);\n"
        + "\n".join(wires)
        + f"\nSupportOriginal source({','.join(source_ports)});\nSupportNormalized actual({','.join(actual_ports)});\n"
        + "assign equivalent="
        + " && ".join(comparisons)
        + ";\nendmodule\n"
    )
    graph = directory / "graph.json"
    logs = prove(
        directory,
        f"read_json {relation}; read_verilog {wrapper}; prep -top FdcSupportNormalization -flatten; opt -full; check -assert; write_json {graph}",
        "equivalent",
        True,
        horizon=0,
        show="-show-inputs",
    )
    guard_inputs(
        json.loads(graph.read_text()),
        "FdcSupportNormalization",
        {n: len(p["bits"]) for n, p in original["ports"].items() if p["direction"] == "input"},
    )
    control = directory / "changed-callback"
    control.mkdir(exist_ok=True)
    corrupt = copy.deepcopy(actual)
    corrupt["ports"]["proofEvent"]["bits"] = ["0"]
    negative = control / "relation.json"
    write_json(negative, {"modules": {"SupportOriginal": original, "SupportNormalized": corrupt}})
    negative_graph = control / "graph.json"
    control_logs = prove(
        control,
        f"read_json {negative}; read_verilog {wrapper}; prep -top FdcSupportNormalization -flatten; opt -full; check -assert; write_json {negative_graph}",
        "equivalent",
        False,
        horizon=0,
        show="-show-inputs",
    )
    guard_inputs(
        json.loads(negative_graph.read_text()),
        "FdcSupportNormalization",
        {n: len(p["bits"]) for n, p in original["ports"].items() if p["direction"] == "input"},
    )
    return (
        deps,
        addresses,
        unknowns,
        [
            negative,
            negative_graph,
            *control_logs,
            normalized,
            relation,
            wrapper,
            graph,
            directory / "prepared.json",
            directory / "prepare.log",
            *logs,
        ],
    )


def fdc_certificate():
    artifacts, inventory = [], []
    for multiplier, path, top in current_machine.parents():
        reset_isolation(path.read_text(), top)
        controller, extra = callback_order.fdc_instance(path.read_text(), top)
        variants = [("positive", controller)]
        if extra:
            anchor = f"if({extra[0]}) begin"
            if controller.count(anchor) < 1:
                raise ValueError("Changed actual FDC enable guards")
            variants.append(("ungated-state", controller.replace(anchor, "if(1'b1) begin")))
        for name, text in variants:
            case = DIRECTORY / f"fdc-{name}{multiplier}"
            case.mkdir(parents=True, exist_ok=True)
            model, raw, cut, contract, graph = [
                case / n for n in ("actual.v", "raw.json", "cut.json", "contract.v", "graph.json")
            ]
            model.write_text(
                re.sub(
                    r'\$readmemb\("([^\"]+)"',
                    lambda m: '$readmemb("' + str(path.parent / m[1]) + '"',
                    text,
                )
            )
            prepared.prepare(
                case,
                f"read_verilog -D SYNTHESIS {model}; hierarchy -top Intel8271; proc; memory_map; opt; dffunmap; check -assert; write_json {raw}",
                [model],
                [raw],
            )
            data = cut_fdc(json.loads(raw.read_text()))
            write_json(cut, data)
            body = data["modules"]["FdcStateNext"]
            deps, address_deps, unknowns, support_artifacts = normalized_support(case, cut, body)
            artifacts.extend(support_artifacts)
            connections = []
            inputs = []
            for port, info in body["ports"].items():
                if info["direction"] != "input":
                    continue
                inputs.append(f"input[{len(info['bits']) - 1}:0] {port}")
                connections.append(f".{port}({port})")
            width = len(body["ports"]["proofState"]["bits"])
            ce = extra[0] if extra else "1'b1"
            indices = {
                name: body["ports"]["proofState"]["bits"].index(body["netnames"][name]["bits"][0])
                for name in ("nmiGap", "nmiLevel")
            }
            contract.write_text(
                "module FdcCallbackHold("
                + ",".join(inputs)
                + ",output equivalent);\n"
                + f"wire[{width - 1}:0] following;wire commandEvent,callbackEvent,abortEvent,pin,directEvent;\nFdcStateNext actual("
                + ",".join(connections)
                + ",.proofNext(following),.proofCommand(commandEvent),.proofEvent(callbackEvent),.proofAbort(abortEvent),.io_nmi(pin),.io_nmiEvent(directEvent));\n"
                + "wire ack=(io_read && io_address==1) || ((io_read || io_write) && io_address[2]);\n"
                + "wire hostReset=io_write && io_address==2 && io_data==1;\n"
                + "wire levelNext=(callbackEvent && !ack && !io_warmReset && !hostReset) || commandEvent;\n"
                + f"wire stage=!{ce} || (following[{indices['nmiGap']}]==(ack && pin) && following[{indices['nmiLevel']}]==levelNext);\n"
                + f"assign equivalent=reset || (({ce} || following==proofState) && (io_write || !commandEvent) &&\n"
                + " ((io_read && io_write) || !ack || !commandEvent) && stage &&\n"
                + " (!hostReset || (!ack && !commandEvent)) &&\n"
                + f" (!abortEvent || (proofState[{indices['nmiLevel']}] && callbackEvent)) &&\n"
                + " (directEvent==(abortEvent && !io_warmReset)) &&\n"
                + f" (pin==((callbackEvent || commandEvent) && !io_warmReset && !proofState[{indices['nmiGap']}])));\nendmodule\n"
            )
            logs = prove(
                case,
                f"read_json {cut}; read_verilog {contract}; prep -top FdcCallbackHold -flatten; opt; check -assert; write_json {graph}",
                "equivalent",
                name == "positive",
                horizon=0,
                show="-show-inputs",
            )
            inventory.append(
                {
                    "clock_multiplier": multiplier,
                    "name": name,
                    "register_bits": width,
                    "callback_support": deps,
                    "address_support": address_deps,
                    "normalized_unknown_bits": unknowns,
                    "normalization_control": True,
                }
            )
            artifacts.extend(
                [
                    model,
                    raw,
                    cut,
                    contract,
                    graph,
                    case / "prepared.json",
                    case / "prepare.log",
                    *logs,
                ]
            )
    return artifacts, inventory


def reset_isolation(source, top):
    parent = bus_binding_proof.module(source, top)
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    controllers = re.findall(r"\bIntel8271\s+(\w+)\s*\(", parent)
    if len(controllers) != 1:
        raise ValueError("Missing unique controller reset interface")
    fdc = bus_binding_proof.ports(parent, "Intel8271", controllers[0])
    header = parent.split(");", 1)[0]
    if (
        sorted(re.findall(r"\binput\s+wire\s+(reset|io_warmReset)\b", header))
        != [
            "io_warmReset",
            "reset",
        ]
        or any(
            cpu.get(port, "").strip() != signal
            for port, signal in (("reset", "reset"), ("warm_reset", "io_warmReset"))
        )
        or any(
            fdc.get(port, "").strip() != signal
            for port, signal in (("reset", "reset"), ("io_warmReset", "io_warmReset"))
        )
    ):
        raise ValueError("Device access can alter actual parent reset bindings")


def qualify():
    current_machine.audit()
    parents = [
        callback_summary.DIRECTORY / "result.json",
        event_composition.DIRECTORY / "result.json",
        explicit_events.DIRECTORY / "result.json",
    ]
    tree = event_composition.audit_tree(parents)
    files = [
        Path(__file__),
        Path(__file__).with_name("callback_cpu_source.v"),
        Path(__file__).with_name("callback_cpu_history.v"),
        *tree,
        foundation.DIRECTORY / "source.v",
        MACRO / "macro-cut.json",
        MACRO / "actual-cut.json",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "build/bbc/ArletCpuEnabled.v",
        current_machine.DIRECTORY / "generation.json",
        *[path for _, path, _ in current_machine.parents()],
        *[blob for _, path, _ in current_machine.parents() for blob in path.parent.glob("*.bin")],
        *[
            Path(module.__file__)
            for module in (
                callback_order,
                event_composition,
                frontend,
                current_machine,
                prepared,
                bus_binding_proof,
            )
        ],
        Path(nmi_sampling.__file__).with_name("nmi_sample.v"),
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    constructor = tree[(foundation.DIRECTORY / "result.json").resolve()]
    prefix = event_composition.check_recipes(
        constructor["default_native_state"], constructor["default_native_pins"]
    )
    artifacts = projection_proof(prefix)
    artifacts.extend(normalization())
    artifacts.extend(histories())
    more, inventory = fdc_certificate()
    artifacts.extend(more)
    event_composition.audit_tree(parents)
    current_machine.audit()
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "audited_receipts": len(tree),
            "fdc_inventory": inventory,
            "query_count": sum(path.name == "proof.log" for path in artifacts),
            "negative_controls": [
                path.parent.name
                for path in artifacts
                if path.name == "proof.log" and "-positive" not in path.parent.name
            ],
            "instruction_count": "unbounded under the declared matching-response/event domain",
            "derivation": [
                "The cold complete-state entry and both all-official-instruction history recipes are recursively audited and regenerated. The new source constructor is proved equal to their exact event projection, specialized only to a common total event and its current pin.",
                "For each actual wait/acceptance block, the sealed all-state event-input CPU refinement preserves identical initialization, proves disabled-event accumulation, and matches the legacy macro on all 168 bits for current event equal to pin rise OR explicit event. The existing wait-block theorem therefore provides the physical macro. Fresh normalization proves any split between retained and current-edge events gives the same next 167 non-pin bits and all functional observations when total events agree. The separately tracked NMI_1 bit equals its own sampled pin.",
                "Fresh complete-source normalization proves the analogous 256-bit C transition identity for matched event injection, comparing trap status and every defined non-pin field plus bus outputs. Stored/raw pin bits are checked separately, not silently excluded.",
                "Fresh induction equates pre/post-access source sampling windows with both base and physical collectors under arbitrary held callbacks, abort events and accepted host device resets. Device reset withdraws the post-access line without clearing an already collected CPU event. The recursively audited actual reset establishment/quiet closure and parent bus separation guarantee a quiet first base edge after reset; an explicit proof monitor records this recovery interval. Every actual 96 MHz FDC state bit holds off-base; static support traces certify callbacks depend only on that state and storage data. Fresh all-state queries bind the exact NMI level/gap D functions, exclude off-base command pulses, and prove reset/acknowledgement/command exclusion. Actual CPU reset and warm-reset inputs remain connected only to their parent reset inputs.",
                "Under matching original/RTL callback-stage events, device reset accesses, response bytes and IRQ levels, normalize both CPU streams to pin zero and all new NMI events in the retained-event input. The existing cold all-instruction common-observer theorem then composes the two canonical histories. Induction over accepted edges lifts equal bus/retirement observations back to their separately tracked source and physical pin histories.",
            ],
            "scope": "Conditional unbounded callback-to-CPU composition: complete C transition is insensitive to raw/stored NMI level after matched event injection except its explicitly tracked pin bits; actual accepted RTL macro transitions agree on all 167 other state bits and every functional bus/register output under matched total events, independently of event split and raw/previous pin. Source traps are compared and observations apply to defined transitions. Fresh initialized induction joins source callback windows to physical collectors at 32/96 MHz, including callbacks during acknowledgement gaps, extra master samples and arbitrarily repeated accepted register-2 device resets. A reset clears the retained device level, preserves collected and current CPU events, and establishes one quiet base interval; its actual all-state establishment/closure certificate is recursively audited. Actual generated FDC state hold, callback-cone support, reset isolation and command-access gating certify the clock-refinement interface.",
            "limits": "Cold post-entry official-opcode CPU domain with no subsequent CPU reset/warm request. Device resets here are accepted register-2 writes, excluding simultaneous acknowledgements or commands as proved by actual address decoding. The recovery monitor suppresses callbacks only through the first following base edge, justified by actual reset quiet closure and the parent bus gap. Callback causes after recovery are freely overapproximated; full original command-state correspondence is not claimed. The all-state actual pin/event certificate retains abortNmi explicitly, audits its held-state/storage cone and proves that an explicit retrigger requires an already high request with completion retained in the event stage. Actual parent binding gates that event to the controller base edge. There is no abort-phase or acceptance-edge exclusion, including abort simultaneous with device reset. Matching response bytes, IRQ levels and callback/abort causes remain premises; the event-window theorem derives matching accepted NMI windows from them and matching device reset accesses. Original-device correspondence to all modeled callback stages is not inferred from the abstract induction. Storage data must settle after a base edge and remain stable at intervening master samples. Full original event-value/timing and retry-state correspondence, external warm resets, actual ROM opcode eligibility, video and whole-game/board equivalence remain open. CPU previous NMI pins are tracked separately. Existing all-instruction CPU history proofs are recursively audited, not rerun.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Callback/CPU composition inputs changed")


if __name__ == "__main__":
    qualify()
