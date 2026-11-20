"""Check callback-before-acknowledgement NMI and post-access VIA IRQ sampling."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof, cpu_events, cpu_interrupt_proof
from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import nmi_sampling
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/cpu-callback-order"


def fdc_instance(source, top):
    parent = bus_binding_proof.module(source, top)
    names = re.findall(r"\bIntel8271\s+(\w+)\s*\(", parent)
    if len(names) != 1:
        raise ValueError("Missing unique actual FDC")
    ports = bus_binding_proof.ports(parent, "Intel8271", names[0])
    cpu = bus_binding_proof.ports(parent, "BbcCpu", "cpu")
    if cpu["nmi"].strip() != ports["io_nmi"].strip():
        raise ValueError("CPU does not sample actual FDC NMI")
    if re.search(r"\bBbcCpuEvents\s+cpu\s*\(", parent) and "nmi_event" not in cpu:
        raise ValueError("Machine CPU is missing its explicit NMI event binding")
    if "nmi_event" in cpu:
        event = cpu["nmi_event"].strip()
        if f"assign {event} = ({ports['io_nmiEvent']} && clock32);" not in parent:
            raise ValueError("CPU does not sample the base-gated actual FDC event")
    if re.fullmatch(r"\w+", cpu["irq"].strip()) is None:
        raise ValueError("Changed parent IRQ endpoint")
    if (
        f"assign {cpu['irq'].strip()} = ((sys_io_irq || user_io_irq) || serial_io_irq);"
        not in parent
    ):
        raise ValueError("Changed actual IRQ source combination")
    controller = bus_binding_proof.module(source, "Intel8271")
    inputs = re.findall(r"input\s+wire\s+(?:\[[^]]+\]\s*)?(\w+)", controller.split(");", 1)[0])
    extra = [
        name
        for name in inputs
        if name
        not in {
            "io_read",
            "io_write",
            "io_warmReset",
            "io_address",
            "io_data",
            "io_storageData",
            "clk",
            "reset",
        }
    ]
    if len(extra) > 1:
        raise ValueError("Changed FDC clock-enable interface")
    if extra and not re.search(
        r"assign\s+" + re.escape(ports[extra[0]]) + r"\s*=\s*\(1'b1 && clock32\);", parent
    ):
        raise ValueError("FDC clock enable differs from actual 32 MHz enable")
    return controller, extra


def native_model(source, top):
    fdc, extra = fdc_instance(source, top)
    fdc = replace_once(
        fdc, "module Intel8271 (", "module Intel8271 (output proofEvent,output proofLevel,"
    )
    fdc = replace_once(
        fdc, "endmodule", "assign proofEvent=eventNmi;assign proofLevel=nmiLevel;\nendmodule"
    )
    core = cpu_events.observed({"Pending": ("", "NMI_edge")})
    enable = f",.{extra[0]}(base)" if extra else ""
    wrapper = f"""module CallbackProbe(input clk,reset,base,rd,wr,input[2:0]address,input[7:0]data,
output[7:0]result,output nmi,eventLevel,storedLevel,pending);
wire retrigger;
Intel8271 f(.clk(clk),.reset(reset),.io_warmReset(1'b0),.io_read(rd),.io_write(wr),
 .io_address(address),.io_data(data),.io_storageData(16'hffff),.io_result(result),.io_nmi(nmi),
 .io_nmiEvent(retrigger),.proofEvent(eventLevel),.proofLevel(storedLevel){enable});
// A held CPU has no instruction advances; its physical edge latch stays live.
BbcCpuEvents cpu(.clk(clk),.reset(reset),.enable(1'b0),.warm_reset(1'b0),.read_data(8'hea),
 .irq(1'b0),.nmi(nmi),.nmi_event(retrigger && base),.proofPending(pending));
endmodule
"""
    return fdc + "\n" + core + "\n" + wrapper


def fdc_contract(controller):
    body = """
wire acknowledgement=(io_read && io_address==1) || ((io_read || io_write) && io_address[2]);
wire proofHostReset=io_write && io_address==2 && io_data==1;
wire proofEquivalent=(io_nmi==((eventNmi || commandNmi) && !io_warmReset && !nmiGap)) &&
 (io_nmiEvent==(abortNmi && !io_warmReset)) &&
 (hostNmi==((eventNmi && !acknowledgement && !io_warmReset && !proofHostReset) || commandNmi));
"""
    return replace_once(controller, "endmodule", body + "endmodule")


def formal_fdc(controller, case, source_directory):
    case.mkdir(parents=True, exist_ok=True)
    model, graph, log = [case / name for name in ("actual.v", "graph.json", "proof.log")]
    text = re.sub(
        r'\$readmemb\("([^\"]+)"',
        lambda m: '$readmemb("' + str(source_directory / m[1]) + '"',
        fdc_contract(controller),
    )
    model.write_text(text)
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog -formal -D SYNTHESIS {model}; hierarchy -top Intel8271; proc; opt_expr -mux_bool; delete -output Intel8271/o:*; cutpoint Intel8271/w:eventNmi Intel8271/w:commandNmi Intel8271/w:nmiGap Intel8271/w:abortNmi; expose Intel8271/w:proofEquivalent; setattr -unset keep Intel8271/w:mode; opt_clean -purge; check -assert; write_json {graph}; sat -prove proofEquivalent 1 -verify -timeout 60",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
    if trial.returncode != 0 or "no model found: SUCCESS!" not in log.read_text():
        raise ValueError("Actual FDC callback/acknowledgement projection differs")
    module = json.loads(graph.read_text())["modules"]["Intel8271"]
    if any(cell["type"] in {"$assume", "$check"} for cell in module["cells"].values()):
        raise ValueError("FDC callback projection acquired an assumption")
    for name in ("eventNmi", "commandNmi", "nmiGap", "abortNmi"):
        bits = module["netnames"][name]["bits"]
        if (
            len(bits) != 1
            or sum(
                cell["type"] == "$anyseq" and cell["connections"]["Y"] == bits
                for cell in module["cells"].values()
            )
            != 1
        ):
            raise ValueError("FDC callback stage is not independently abstracted: " + name)
    return [model, graph, log]


def via_cut(graph):
    """Observe the real normal-edge flag/mask D functions, cutting every Q."""
    body = graph["modules"]["Via6522"]
    state, next_by_q = [], {}
    for name, cell in list(body["cells"].items()):
        if cell["type"] not in {"$dff", "$adff"}:
            if any(token in cell["type"].lower() for token in ("ff", "latch", "mem")):
                raise ValueError("Uninventoried VIA state")
            continue
        ports = cell["connections"]
        if (
            ports["CLK"] != body["ports"]["clk"]["bits"]
            or int(cell["parameters"]["CLK_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed VIA clock edge")
        if cell["type"] == "$adff" and (
            ports["ARST"] != body["ports"]["reset"]["bits"]
            or int(cell["parameters"]["ARST_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed VIA asynchronous reset")
        for q, d in zip(ports["Q"], ports["D"], strict=True):
            state.append(q)
            next_by_q[q] = d
        del body["cells"][name]
    if len(set(state)) != len(state) or not state:
        raise ValueError("Incomplete VIA register cut")
    for wire in body["netnames"].values():
        wire["attributes"].pop("init", None)
    for name in ("flags", "enabled"):
        bits = body["netnames"][name]["bits"]
        if len(bits) != 7 or not set(bits).issubset(next_by_q):
            raise ValueError("Changed VIA interrupt state")
        body["ports"]["proofNext" + name] = {
            "direction": "output",
            "bits": [next_by_q[bit] for bit in bits],
        }
    body["ports"]["proofState"] = {"direction": "input", "bits": state}
    body["attributes"]["top"] = "0"
    return {"modules": {"ViaNext": body}}


def formal_via(source, case, positive=True):
    case.mkdir(parents=True, exist_ok=True)
    model, raw, cut, prop, log = [
        case / name for name in ("actual.v", "raw.json", "cut.json", "contract.v", "proof.log")
    ]
    modules = "\n".join(
        bus_binding_proof.module(source, name)
        for name in ("Via6522", "ViaSerial", "ViaControlPins")
    )
    model.write_text(modules)
    with (case / "prepare.log").open("w") as output:
        subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog -D SYNTHESIS {model}; hierarchy -top Via6522; flatten; proc; memory_map; opt_clean; write_json {raw}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
    graph = via_cut(json.loads(raw.read_text()))
    write_json(cut, graph)
    inputs, connections = [], []
    for name, port in graph["modules"]["ViaNext"]["ports"].items():
        if port["direction"] == "input":
            width = len(port["bits"])
            inputs.append(f"input [{width - 1}:0] {name}")
            connections.append(f".{name}({name})")
    prop.write_text(
        "module ViaIrqOrder("
        + ",".join(inputs)
        + ",output equivalent);\nwire irq;wire[6:0] flags,enabled;\nViaNext actual("
        + ",".join(connections)
        + ",.io_irq(irq),.proofNextflags(flags),.proofNextenabled(enabled));\nassign equivalent=reset || (irq==(|(flags & enabled)));\nendmodule\n"
    )
    witness = case / "witness.json"
    witness.unlink(missing_ok=True)
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {cut}; read_verilog {prop}; prep -top ViaIrqOrder -flatten; check -assert; sat -prove equivalent 1 -verify -timeout 60 -show equivalent -dump_json {witness}",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
    accepted = (
        trial.returncode == 0 and "no model found: SUCCESS!" in log.read_text()
        if positive
        else trial.returncode != 0
        and "ERROR: Called with -verify and proof did fail!" in log.read_text()
        and witness.is_file()
        and any(
            s["name"] == "equivalent" and "0" in s.get("wave", "")
            for s in json.loads(witness.read_text())["signal"]
        )
    )
    if not accepted:
        raise ValueError("Actual VIA IRQ does not observe post-access interrupt state")
    return [
        model,
        raw,
        cut,
        prop,
        log,
        case / "prepare.log",
        *([witness] if witness.is_file() else []),
    ]


def compile_native(model, case, top="CallbackProbe", driver=None, dependencies=()):
    binary = case / ("V" + top)
    driver = driver or ROOT / "sim/native/bbc_callback_order_test.cpp"
    key = hashes(
        [
            model,
            driver,
            *dependencies,
            Path(__file__),
            Path(shutil.which("verilator")),
            Path(shutil.which("c++")),
        ]
    )
    receipt = case / "build.json"
    saved = json.loads(receipt.read_text()) if receipt.exists() else {}
    if saved.get("inputs") != key or saved.get("binary") != hashes([binary]):
        with (case / "build.log").open("w") as output:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    "-j",
                    "2",
                    "-Wno-fatal",
                    "--top-module",
                    top,
                    "--Mdir",
                    str(case),
                    "-CFLAGS",
                    "-O2 -std=c++17 -Werror=return-type -I" + str(ROOT / "sim/native"),
                    str(model),
                    str(driver),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        write_json(receipt, {"inputs": key, "binary": hashes([binary])})
    return binary


def qualify():
    from tools.bbc.proofs.cpu_reference import fdc_order

    current_machine.audit()
    serial_path = fdc_order.DIRECTORY / "result.json"
    serial = memory_composition.sealed(serial_path)
    control = [
        check
        for check in serial.get("checks", [])
        if check.get("clock_multiplier") == 1 and check.get("name") == "native-post-ack-nmi"
    ]
    if len(control) != 1 or control[0].get("accepted") is not True:
        raise ValueError("Missing initialized rephased-byte NMI suppression counterexample")
    for _, path, _ in current_machine.parents():
        if any(serial["inputs"].get(name) != value for name, value in hashes([path]).items()):
            raise ValueError("Serial acknowledgement premise uses a different actual parent")
    parent = nmi_sampling.DIRECTORY / "result.json"
    memory_composition.sealed(parent)
    clock_paths = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for (multiplier, path, _), clock_path in zip(
        current_machine.parents(), clock_paths, strict=True
    ):
        clock = memory_composition.sealed(clock_path)
        if (
            clock.get("clock_multiplier") != multiplier
            or clock.get("negative_control_detected") is not True
            or clock["inputs"].get(str(path.relative_to(ROOT)))
            != hashes([path])[str(path.relative_to(ROOT))]
        ):
            raise ValueError("FDC callback clock evidence binds a different parent")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    files = [
        Path(__file__),
        serial_path,
        helper,
        ROOT / "sim/native/bbc_callback_order_test.cpp",
        ROOT / "rtl/bbc/Intel8271.scala",
        ROOT / "rtl/bbc/Via6522.scala",
        ROOT / "rtl/bbc/BbcCpu.v",
        ROOT / "build/bbc/ArletCpuEnabled.v",
        *cpu_events.files(),
        parent,
        *clock_paths,
        ROOT / "tools/bbc/reference_cycle.mjs",
        current_machine.DIRECTORY / "generation.json",
        *[path for _, path, _ in current_machine.parents()],
        *[blob for _, path, _ in current_machine.parents() for blob in path.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        Path(current_machine.__file__),
        Path(bus_binding_proof.__file__),
        Path(cpu_interrupt_proof.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(name)) for name in ("yosys", "node", "verilator", "c++")],
    ]
    before = hashes(files)
    subprocess.run(
        [
            "node",
            str(helper),
            str(reference),
            str(ROOT / "tools/bbc/reference_cycle.mjs"),
            str(DIRECTORY),
        ],
        check=True,
        timeout=30,
    )
    original = json.loads((DIRECTORY / "source.json").read_text())
    if (
        original.get("passed") is not True
        or len(original["cases"]) != 16
        or original["irq_cases"] != 16384
    ):
        raise ValueError("Incomplete original callback inventory")
    artifacts = [DIRECTORY / name for name in ("source.json", "fdc.tsv", "engine-proxy.cjs")]
    checks = []
    for multiplier, path, top in current_machine.parents():
        source = path.read_text()
        controller, _ = fdc_instance(source, top)
        artifacts.extend(formal_fdc(controller, DIRECTORY / f"fdc-formal{multiplier}", path.parent))
        artifacts.extend(formal_via(source, DIRECTORY / f"via-formal{multiplier}"))
        old_irq = replace_once(
            source,
            "assign io_irq = (|(nextFlags & nextEnabled));",
            "assign io_irq = (|(flags & enabled));",
        )
        artifacts.extend(
            formal_via(old_irq, DIRECTORY / f"via-pre-access-fault{multiplier}", positive=False)
        )
        variants = [("positive", source)]
        match = re.findall(
            r"assign (\w+) = \(\(io_read \|\| io_write\) && dataAccess\);", controller
        )
        if len(match) != 1:
            raise ValueError("Changed data acknowledgement anchor")
        gate = f"assign {match[0]} = ((io_read || io_write) && dataAccess);"
        variants.append(
            (
                "data-ack-needs-request",
                replace_once(
                    source, gate, gate.replace("&& dataAccess)", "&& dataAccess && eventRequest)")
                ),
            )
        )
        for name, body in variants:
            case = DIRECTORY / f"{name}{multiplier}"
            case.mkdir(exist_ok=True)
            text = native_model(body, top)
            text = re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(path.parent / m[1]) + '"',
                text,
            )
            model = case / "actual.v"
            model.write_text(text)
            binary = compile_native(model, case)
            trial = subprocess.run(
                [str(binary), str(DIRECTORY / "fdc.tsv"), str(multiplier)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            log = case / "run.log"
            log.write_text(trial.stdout + trial.stderr)
            accepted = (
                (
                    trial.returncode == 0
                    and trial.stdout == "Callback ordering: 16 cold cases; 0 differences\n"
                )
                if name == "positive"
                else (trial.returncode == 1 and trial.stdout.startswith("CALLBACK_MISMATCH case "))
            )
            checks.append({"clock_multiplier": multiplier, "name": name, "accepted": accepted})
            artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
            print(
                f"Callback ordering {multiplier} {name}: " + ("PASS" if accepted else "FAIL"),
                flush=True,
            )
            if not accepted:
                raise ValueError("Source/device/CPU callback boundary check incomplete")
        if multiplier == 1:
            checks.append(
                {
                    "clock_multiplier": 1,
                    "name": "post-ack-nmi",
                    "accepted": True,
                    "receipt": str(serial_path.relative_to(ROOT)),
                    "case": "rephased-byte-ack",
                }
            )
    current_machine.audit()
    memory_composition.sealed(parent)
    memory_composition.sealed(serial_path)
    for clock_path in clock_paths:
        memory_composition.sealed(clock_path)
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs_unchanged": stable,
        "inputs": before,
        "artifacts": hashes(artifacts),
        "checks": checks,
        "source_fdc_cases": 16,
        "source_via_irq_cases": 16384,
        "via_pre_access_faults_detected": [1, 3],
        "scope": "Original cold FDC command histories, actual cycle-adapter callback/sample/bus/tick order, actual generated FDC and physical CPU NMI latch at 32/96 MHz. Result and data acknowledgements before/on/after the real completion callback; no executing RTL state is assigned. All-input FDC callback/host/command level projection cuts the peripheral and command event stages independently; all-state actual VIA IRQ versus its flag/mask D functions is proved separately. Original VIA updateIFR executes every 7-bit flag/mask pairing. The sealed rephased-byte history supplies the initialized 32 MHz historical post-ack NMI counterexample; ordinary aligned mechanical expiry can be retained before host acknowledgement. Old request-gated data acknowledgements must fail at both rates.",
        "limits": "Local callback/acknowledgement boundary and finite cold histories, not universal FDC/VIA/serial event generation or full gameplay. Native CPU is held without instruction advances to expose its physical edge latch. The adapter uses synthetic bus pins only to observe ordering, not as CPU-equivalence evidence. Absolute source epoch, complete interrupt-history and event-separation composition, arbitrary warm resets, instruction eligibility and whole-machine/game equivalence remain separate.",
        "universal_game_equivalence": False,
    }
    write_json(DIRECTORY / "result.json", report)
    if not stable:
        raise ValueError("Callback proof inputs changed")
    return report


if __name__ == "__main__":
    qualify()
