"""Prove restart support preserves every existing CPU transition when unused."""

import json
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, prepare

BASELINE = ROOT / "sim/formal/BbcCpuColdBaseline.v"
BASELINE_SHA256 = "0430515527171f044fe4bb0e29aff4ed9c7c11e37ba8a1659f971c022c5a90c4"


def state_model(graph):
    """Cut every actual register at Q/D, retaining all synthesized logic."""
    module = graph["modules"]["BbcCpu"]
    state, following, primitives = [], {}, {}
    for name, cell in list(module["cells"].items()):
        kind = cell["type"]
        if kind not in ("$dff", "$adff"):
            if any(token in kind.lower() for token in ("ff", "latch", "mem")):
                raise ValueError("Unrecognized CPU state primitive: " + kind)
            continue
        ports, parameters = cell["connections"], cell["parameters"]
        if (
            ports["CLK"] != module["ports"]["clk"]["bits"]
            or int(parameters["CLK_POLARITY"], 2) != 1
            or len(ports["Q"]) != len(ports["D"])
        ):
            raise ValueError("Changed CPU register clock or width")
        if kind == "$adff" and (
            ports["ARST"] != module["ports"]["reset"]["bits"]
            or int(parameters["ARST_POLARITY"], 2) != 1
        ):
            raise ValueError("Changed CPU asynchronous reset")
        for index, (q, d) in enumerate(zip(ports["Q"], ports["D"], strict=True)):
            if q in following:
                raise ValueError("Duplicate CPU state driver")
            state.append(q)
            following[q] = d
            primitives[q] = (
                kind,
                parameters["ARST_VALUE"][::-1][index] if kind == "$adff" else None,
            )
        del module["cells"][name]
    if not state or len(state) != len(set(state)):
        raise ValueError("Incomplete CPU state inventory")
    initial = {}
    for wire in module["netnames"].values():
        value = wire["attributes"].pop("init", None)
        if value is not None:
            for bit, literal in zip(wire["bits"], value[::-1], strict=True):
                if bit in initial and initial[bit] != literal:
                    raise ValueError("Conflicting CPU initialization aliases")
                initial[bit] = literal
    return module, state, following, primitives, initial


def state_aliases(module, state):
    aliases = []
    for bit in state:
        names = [
            (name, index)
            for name, wire in module["netnames"].items()
            if name.startswith("core.") or name == "reset_dummy"
            for index, value in enumerate(wire["bits"])
            if value == bit
        ]
        if not names:
            raise ValueError("CPU state bit has no semantic binding")
        aliases.append(sorted(names, key=lambda pair: (pair[0].startswith("core.$"), pair[0]))[0])
    return aliases


def relational_model(before_graph, after_graph):
    old, old_state, old_d, old_kind, old_init = state_model(before_graph)
    new, new_state, new_d, new_kind, new_init = state_model(after_graph)
    aliases = state_aliases(old, old_state)
    common = [new["netnames"][name]["bits"][index] for name, index in aliases]
    if len(common) != len(set(common)) or any(bit not in new_d for bit in common):
        raise ValueError("Changed or missing existing CPU state bit")
    for old_bit, new_bit in zip(old_state, common, strict=True):
        if old_kind[old_bit] != new_kind[new_bit] or old_init.get(old_bit) != new_init.get(new_bit):
            raise ValueError("Changed existing CPU reset or initialization")
    extra = [bit for bit in new_state if bit not in common]
    pending, second = (
        extra.index(new["netnames"][name]["bits"][0]) for name in ("warm_pending", "warm_second")
    )
    if any(new_init.get(extra[index]) != "0" for index in (pending, second)):
        raise ValueError("Restart control must initialize inactive")
    old_inputs = {name: port for name, port in old["ports"].items() if port["direction"] == "input"}
    new_inputs = {name: port for name, port in new["ports"].items() if port["direction"] == "input"}
    if set(new_inputs) != set(old_inputs) | {"warm_reset"} or any(
        len(old_inputs[name]["bits"]) != len(new_inputs[name]["bits"]) for name in old_inputs
    ):
        raise ValueError("Unrecognized CPU input relation")
    outputs = [name for name, port in sorted(old["ports"].items()) if port["direction"] == "output"]
    if any(len(old["ports"][name]["bits"]) != len(new["ports"][name]["bits"]) for name in outputs):
        raise ValueError("Changed existing CPU output width")
    for module, q, d, other in ((old, old_state, old_d, []), (new, common, new_d, extra)):
        module["ports"]["common_state"] = {"direction": "input", "bits": q}
        module["ports"]["next_common"] = {"direction": "output", "bits": [d[bit] for bit in q]}
        module["ports"]["observations"] = {
            "direction": "output",
            "bits": [bit for name in outputs for bit in module["ports"][name]["bits"]],
        }
        if other:
            module["ports"]["other_state"] = {"direction": "input", "bits": other}
            module["ports"]["next_other"] = {
                "direction": "output",
                "bits": [d[bit] for bit in other],
            }
    inventory = {
        "common_register_bits": len(old_state),
        "new_register_bits": len(extra),
        "excluded_existing_register_bits": 0,
        "output_bits": len(old["ports"]["observations"]["bits"]),
        "bindings": aliases,
        "inactive_restart_bits": [pending, second],
    }
    return {"modules": {"Before": old, "After": new}}, inventory


def qualify():
    prepare.main()
    if digest(BASELINE) != BASELINE_SHA256:
        raise ValueError("Frozen CPU preservation baseline changed")
    directory = build.BBC / "cpu-cold-preservation"
    directory.mkdir(exist_ok=True)
    wrapper, core = ROOT / "rtl/bbc/BbcCpu.v", build.BBC / "ArletCpuEnabled.v"
    sources = [
        Path(__file__),
        BASELINE,
        wrapper,
        core,
        ROOT / "tools/bbc/prepare.py",
        ROOT / "tools/bbc/cpu_bus.py",
    ]
    before = build.hashes(sources)
    version = subprocess.check_output(["yosys", "-V"], text=True).strip()

    def prepare_graph(paths, name):
        output = directory / (name + ".json")
        with (directory / (name + "-prepare.log")).open("w") as log:
            subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_verilog {' '.join(map(str, paths))}; hierarchy -top BbcCpu; flatten; proc; memory_map; opt; dffunmap; write_json {output}",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=60,
            )
        return json.loads(output.read_text())

    baseline_graph = prepare_graph([BASELINE], "baseline")
    results = []
    for name, model in (
        ("actual", wrapper),
        ("wrong-write-output", directory / "wrong-write-output.v"),
    ):
        if name != "actual":
            text = wrapper.read_text()
            target = "assign write_enable = reset_dummy == 0 && !warm_second && core_write;"
            if text.count(target) != 1:
                raise ValueError("Changed CPU preservation fault anchor")
            model.write_text(
                text.replace(
                    target,
                    "assign write_enable = !(reset_dummy == 0 && !warm_second && core_write);",
                )
            )
        import copy

        graph, inventory = relational_model(
            copy.deepcopy(baseline_graph), prepare_graph([model, core], name)
        )
        design = directory / (name + "-combinational.json")
        write_json(design, graph)
        n, a, o = (
            inventory["common_register_bits"],
            inventory["new_register_bits"],
            inventory["output_bits"],
        )
        pending, second = inventory["inactive_restart_bits"]
        miter = directory / (name + "-miter.v")
        miter.write_text(f"""module ColdPreserve(input clk,reset,enable,irq,nmi,input[7:0]read_data,input[{n - 1}:0]state,input[{a - 1}:0]aux,output equivalent);
wire[{n - 1}:0]beforeNext,afterNext;wire[{a - 1}:0]auxNext;wire[{o - 1}:0]beforeOut,afterOut;
Before beforeModel(.clk(clk),.reset(reset),.enable(enable),.irq(irq),.nmi(nmi),.read_data(read_data),.common_state(state),.next_common(beforeNext),.observations(beforeOut));
After afterModel(.clk(clk),.reset(reset),.enable(enable),.irq(irq),.nmi(nmi),.read_data(read_data),.warm_reset(1'b0),.common_state(state),.other_state(aux),.next_common(afterNext),.next_other(auxNext),.observations(afterOut));
assign equivalent=(aux[{pending}]||aux[{second}])||((beforeNext==afterNext)&&(beforeOut==afterOut)&&!auxNext[{pending}]&&!auxNext[{second}]);
endmodule
""")
        log = directory / (name + "-proof.log")
        with log.open("w") as stream:
            result = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {design}; read_verilog {miter}; hierarchy -top ColdPreserve; flatten; opt; sat -prove equivalent 1 {'-verify' if name == 'actual' else ''} -show-inputs -show-outputs",
                ],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
        expected = (
            result.returncode == 0
            if name == "actual"
            else result.returncode == 0 and "model found: FAIL" in log.read_text()
        )
        results.append({"name": name, "expected_result": expected, "inventory": inventory})
    stable = before == build.hashes(sources)
    receipt = {
        "passed": stable and all(r["expected_result"] for r in results),
        "inputs": before,
        "inputs_unchanged": stable,
        "yosys": version,
        "results": results,
        "artifacts": build.hashes(
            [*directory.glob("*.json"), *directory.glob("*.v"), *directory.glob("*.log")]
        ),
        "scope": "Relational induction step: every baseline state bit has a unique matching actual Q/D binding; all existing outputs and next-state bits equal for arbitrary shared current CPU state, enable, read data, IRQ, NMI and reset when restart requests remain low and the initialized restart controls are inactive. Those controls remain inactive. Other new state is arbitrary. Existing initial values and asynchronous-reset actions also match. Thus any history beginning with matching declared digital initialization preserves the baseline observations; no baseline register is excluded.",
        "limits": "Preservation against the frozen previous RTL, whose independent correctness evidence has separate scope. This does not prove the baseline universally correct, any warm-request history, new SYNC output, FDC, parent wiring or universal game equivalence.",
        "universal_game_equivalence": False,
    }
    # Do not include a previous result in the inventory of this new receipt.
    receipt["artifacts"].pop(str((directory / "result.json").relative_to(ROOT)), None)
    write_json(directory / "result.json", receipt)
    if not receipt["passed"]:
        raise ValueError("Cold CPU preservation failed; complete models retained")
    print(
        f"CPU cold preservation: {n} existing state bits, {o} output bits; zero exclusions",
        flush=True,
    )
    return receipt


if __name__ == "__main__":
    qualify()
