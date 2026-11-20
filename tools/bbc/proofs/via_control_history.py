"""Prove complete VIA control-pin state histories and parent write endpoints."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.bbc.proofs import pin_bindings
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, source_root, write_json


def run_proof(work, name, command, positive, target, cwd):
    witness = work / (name + "-counterexample.json")
    witness.unlink(missing_ok=True)
    log = work / (name + ".log")
    with log.open("w") as output:
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=cwd,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    content = log.read_text()
    passed = (
        result.returncode == 0
        and ("Induction step proven: SUCCESS!" in content or "no model found: SUCCESS!" in content)
        if positive
        else result.returncode != 0
        and "ERROR: Called with -verify and proof did fail!" in content
        and witness.is_file()
        and any(
            signal["name"] == target and "0" in signal.get("wave", "")
            for signal in json.loads(witness.read_text())["signal"]
        )
    )
    if not passed:
        raise ValueError("VIA control-pin proof failed: " + name)
    print(name + ": PASS", flush=True)
    return {"name": name, "accepted": passed, "log_sha256": digest(log)}


def qualify():
    directory = build.BBC / "via-control-history-contract"
    directory.mkdir(exist_ok=True)
    work = Path("/tmp/fpgaleet-via-control-history")
    work.mkdir(exist_ok=True)
    parents = [
        (build.BBC / "BbcMachine.v", "BbcMachine"),
        (video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    modules = [bus_binding_proof.module(path.read_text(), "ViaControlPins") for path, _ in parents]
    if modules[0] != modules[1]:
        raise ValueError("Control-pin children differ between parent clocks")
    sources = [
        ROOT / "rtl/bbc" / name
        for name in (
            "ViaControlPins.scala",
            "Via6522.scala",
            "BbcMachine.scala",
            "BbcAddressDecode.scala",
        )
    ]
    receipts = [path.parent / "elaboration.json" for path, _ in parents]
    for (path, _), receipt in zip(parents, receipts, strict=True):
        recorded = json.loads(receipt.read_text())
        for source in sources:
            if recorded["inputs"].get(str(source.relative_to(ROOT))) != digest(source):
                raise ValueError("Changed control-pin or endpoint elaboration source")
        if recorded["outputs"].get(str(path.relative_to(ROOT))) != digest(path):
            raise ValueError("Changed emitted parent")
    reference = ROOT / "tools/bbc/reference_via_control.mjs"
    native = build.BBC / "via-control"
    saved = json.loads((native / "result.json").read_text())
    if not saved.get("passed") or any(
        digest(ROOT / name) != sha for name, sha in saved["artifacts"].items()
    ):
        raise ValueError("Changed sealed native VIA control-pin comparison")
    contract = Path(__file__).with_name("via_control_contract.v")
    original = source_root("jsbeeb")
    files = [
        Path(__file__),
        contract,
        Path(pin_bindings.__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        *sources,
        *receipts,
        *[path for path, _ in parents],
        reference,
        native / "result.json",
        native / "VVia6522",
        native / "devices.v",
        native / "pins.bin",
        ROOT / "sim/native/bbc_via_control_test.cpp",
        ROOT / "tools/validation/reference.lock.json",
        *sorted((original / "src").rglob("*.js")),
        Path(shutil.which("yosys")),
        Path(shutil.which("node")),
        *build.BBC.glob("*.bin"),
        *video96.DIRECTORY.glob("*.bin"),
    ]
    before = hashes(files)
    fixture = work / "pins.bin"
    generated = subprocess.run(
        ["node", str(reference), str(original), str(fixture)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    if json.loads(generated.stdout) != {"cases": 1024, "observations": 153600} or digest(
        fixture
    ) != digest(native / "pins.bin"):
        raise ValueError("Independent VIA control fixture changed")
    actual_probe = native / "devices.v"
    if bus_binding_proof.module(actual_probe.read_text(), "ViaControlPins") != modules[0]:
        raise ValueError("Native control-pin probe differs from actual parents")
    comparison = subprocess.run(
        [str(native / "VVia6522"), str(fixture)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    if (
        comparison.stdout.strip()
        != "VIA control pins: 153600 observations in 1024 cases; 0 differences"
    ):
        raise ValueError("Incomplete original public control-pin comparison")
    (work / "native-comparison.log").write_text(comparison.stdout + comparison.stderr)
    actual = modules[0]
    header = actual.index(");")
    observed = actual[:header] + ", output wire [13:0] proofState\n" + actual[header:]
    observed = replace_once(
        observed,
        "endmodule",
        "assign proofState={pcr,ca2,cb2,pulseA,pulseB,previousCa1,previousCb1};\nendmodule",
    )
    variants = [
        ("positive", observed),
        ("lost-ca2-pulse-expiry", replace_once(observed, "(io_tick && pulseA)", "1'b0")),
        ("lost-cb1-completion", replace_once(observed, "|| io_cb1Pulse", "|| 1'b0")),
        (
            "lost-coincident-rise",
            replace_once(
                observed,
                "assign io_ca2Rise = ((((! ca2) && beforeWriteA) && pcr[3]) || (((! beforeWriteA) && nextA) && nextPcr[3]));",
                "assign io_ca2Rise = ((! ca2) && nextA && nextPcr[3]);",
            ),
        ),
        (
            "delayed-pcr-output",
            replace_once(
                observed, "assign io_cb2Output = pcr[7];", "assign io_cb2Output = previousCb1;"
            ),
        ),
    ]
    proofs = []
    for name, source in variants:
        rtl = work / (name + ".v")
        rtl.write_text(source)
        mode = "-tempinduct -initsteps 2 -maxsteps 6" if name == "positive" else "-seq 8"
        command = f"read_verilog -formal {rtl} {contract}; prep -top ViaControlContract -flatten; check -assert; opt; async2sync; opt_clean; write_json {work}/{name}.json; sat -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 -show equivalent -dump_json {work}/{name}-counterexample.json"
        proofs.append(run_proof(work, name, command, name == "positive", "equivalent", ROOT))
    graph = json.loads((work / "positive.json").read_text())
    cells = graph["modules"]["ViaControlContract"]["cells"].values()
    if sum(len(cell["connections"]["Q"]) for cell in cells if "ff" in cell["type"]) != 28:
        raise ValueError("Changed complete actual/reference control-pin state inventory")
    endpoints = []
    for multiplier, (path, top) in zip((1, 3), parents, strict=True):
        original_parent = path.read_text()
        _, removed, cuts = pin_bindings.observe(original_parent, top)
        cuts = [name for name in cuts if name not in {"cpuEnable", "tickVia"}]
        parent = bus_binding_proof.module(original_parent, top)
        observed_parent = (
            parent.removesuffix("endmodule")
            + "\nwire proofEndpoint=(!sys_io_write && !user_io_write) || tickVia;\nendmodule"
        )
        accepted_parent = replace_once(original_parent, parent, observed_parent)
        rows = []
        for name, source in (
            ("positive", accepted_parent),
            (
                "lost-slow-gate",
                replace_once(
                    accepted_parent,
                    "assign cpuEnable = (tickCpu && ((! decode_io_slow) || (tickVia && delayedSlow)));",
                    "assign cpuEnable = tickCpu;",
                ),
            ),
        ):
            label = f"endpoint-{multiplier}-{name}"
            rtl = work / (label + ".v")
            rtl.write_text(source)
            mode = "-tempinduct -initsteps 2 -maxsteps 6" if name == "positive" else "-seq 100"
            command = (
                f"read_verilog -formal -D SYNTHESIS {rtl}; delete "
                + " ".join(f"{top}/c:{child}" for child in removed)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + f"; expose {top}/w:proofEndpoint; opt -purge; flatten; opt; async2sync; opt_clean; check -assert; write_json {work}/{label}.json; sat -set-init-zero {mode} -prove proofEndpoint 1 -verify -timeout 120 -show proofEndpoint -dump_json {work}/{label}-counterexample.json"
            )
            rows.append(
                run_proof(work, label, command, name == "positive", "proofEndpoint", path.parent)
            )
        endpoints.append({"clock_multiplier": multiplier, "checks": rows})
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([path for path in work.iterdir() if path.is_file()]),
            "register_bits": 14,
            "output_bits": 16,
            "original_reference_observations": 153600,
            "proofs": proofs,
            "parent_endpoints": endpoints,
            "scope": "All actual ViaControlPins sequential state and pin/PCR/direction/edge outputs over arbitrary reset, tick, accepted write, data, CA1/CB1 and completion-pulse histories. Both identical parent children are bound. Actual parent address decoder, slow-access gate and tick equations prove VIA writes imply the one-MHz tick, without assumptions or cutting the acceptance/tick signals. Four real child faults and a parent slow-gate fault at both clocks require initialized counterexamples. Independent unmodified public jsbeeb fixtures cover 1024 cases and 153600 transitions.",
            "limits": "The history contract expresses declared callback-before-write ordering; finite source fixtures do not universally prove JavaScript scheduler/API equivalence. Parent child outputs are independent cut inputs. Input-pin interrupt production, serial/timer state, global reference phase and whole-game composition remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Control-pin proof inputs changed")


if __name__ == "__main__":
    qualify()
