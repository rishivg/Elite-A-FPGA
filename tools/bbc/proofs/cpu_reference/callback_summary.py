"""Unbounded conditional composition of callback stages and NMI event windows."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine
from tools.bbc.proofs.cpu_reference import (
    callback_order,
    device_epoch,
    event_composition,
    nmi_sampling,
    prepared,
    zero_dispatch,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-callback-summary"
TOP = "CallbackSummary"


def guard_domain(graph):
    module = graph["modules"][TOP]
    if any(cell["type"] in {"$assume", "$check"} for cell in module["cells"].values()):
        raise ValueError("Callback event summary acquired an assumption")
    for name, width, kind in (
        ("initialState", 256, "$anyconst"),
        *[
            (name, 1, "$anyseq")
            for name in (
                "accepted",
                "peripheralEvent",
                "acknowledge",
                "commandEvent",
                "abortEvent",
                "resetRequest",
            )
        ],
    ):
        wire = module["netnames"][name]
        if (
            len(wire["bits"]) != width
            or "init" in wire["attributes"]
            or sum(
                cell["type"] == kind and cell["connections"]["Y"] == wire["bits"]
                for cell in module["cells"].values()
            )
            != 1
        ):
            raise ValueError("Callback event summary narrowed its input: " + name)


def prove():
    model = Path(__file__).with_suffix(".v")
    sampler = Path(nmi_sampling.__file__).with_name("nmi_sample.v")
    body = model.read_text()
    variants = [
        ("positive", body),
        (
            "post-ack-pin",
            replace_once(
                body,
                "wire pin=(pre || commandNmi) && !gap;",
                "wire pin=post && !gap;",
            ),
        ),
        (
            "lost-command-edge",
            replace_once(
                body,
                "wire pin=(pre || commandNmi) && !gap;",
                "wire pin=pre && !gap;",
            ),
        ),
        (
            "missing-bus-gap",
            replace_once(body, " && !(previousAccepted && accepted)", ""),
        ),
        ("lost-abort-event", replace_once(body, "wire delivered=retrigger;", "wire delivered=0;")),
        (
            "lost-accepted-abort",
            replace_once(
                body, "wire delivered=retrigger;", "wire delivered=retrigger && !accepted;"
            ),
        ),
    ]
    variants.extend(reset_faults(body, "peripheralEvent"))
    artifacts, checks = [], []
    for name, text in variants:
        case = DIRECTORY / name
        case.mkdir(parents=True, exist_ok=True)
        contract, graph, log, witness = [
            case / filename
            for filename in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        contract.write_text(text)
        prepared.prepare(
            case,
            f"read_verilog -formal -sv {sampler} {contract}; prep -top {TOP} -flatten; check -assert; opt; write_json {graph}",
            [sampler, contract],
            [graph],
        )
        guard_domain(json.loads(graph.read_text()))
        witness.unlink(missing_ok=True)
        positive = name == "positive"
        property_name = "equivalent" if positive else "observation"
        horizon = "-tempinduct -initsteps 2 -maxsteps 8" if positive else "-seq 6"
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-T",
                    "-p",
                    f"read_json {graph}; sat -set-init-zero {horizon} -prove {property_name} 1 -verify -timeout 60 -show equivalent -show observation -show edgeEligible -show accepted -show pre -show post -show pin -show rise -show gap -show resetAccess -show resetPending -show pending -dump_json {witness}",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=90,
            )
        output = log.read_text()
        accepted = (
            trial.returncode == 0 and "Induction step proven: SUCCESS!" in output
            if positive
            else trial.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in output
            and witness.is_file()
            and any(
                signal["name"] == "observation" and "0" in signal.get("wave", "")
                for signal in json.loads(witness.read_text())["signal"]
            )
        )
        checks.append({"name": name, "accepted": accepted})
        artifacts.extend([contract, graph, log, case / "prepared.json", case / "prepare.log"])
        if witness.is_file():
            artifacts.append(witness)
        print("Callback event summary " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            raise ValueError("Callback event summary incomplete: " + name)
    return checks, artifacts


def reset_faults(body, callback):
    return [
        (
            "lost-reset-edge",
            replace_once(
                body,
                "wire rise=(pin && !previous) || delivered;",
                "wire rise=((pin && !previous) || delivered) && !resetAccess;",
            ),
        ),
        (
            "lost-reset-pending",
            replace_once(
                body,
                "postSample[56]==(pending || rise)",
                "postSample[56]==((pending && !resetAccess) || rise)",
            ),
        ),
        (
            "missing-reset-recovery",
            replace_once(body, f"({callback} && !resetPending)", callback),
        ),
    ]


def guard_reset_premise(report):
    expected = [
        {"clock_multiplier": m, "name": name, "accepted": True, "register_bits": 934}
        for m in (1, 3)
        for name in ("positive", "callback-overrides-reset-mode", "stale-phase", "stale-parameters")
    ]
    if report.get("checks") != expected or report.get("source", {}).get("dispatch_cases") != 2048:
        raise ValueError("Incomplete actual reset establishment/quiet closure premise")


def qualify():
    from tools.bbc.proofs.cpu_reference import fdc_reset

    current_machine.audit()
    reset_path = fdc_reset.DIRECTORY / "result.json"
    parents = [
        module.DIRECTORY / "result.json"
        for module in (callback_order, zero_dispatch, nmi_sampling, event_composition, device_epoch)
    ]
    parents.append(reset_path)
    tree = event_composition.audit_tree(parents)
    guard_reset_premise(tree[reset_path.resolve()])
    files = [
        Path(__file__),
        Path(__file__).with_suffix(".v"),
        Path(nmi_sampling.__file__).with_name("nmi_sample.v"),
        *tree,
        *[Path(module.__file__) for module in (event_composition, prepared)],
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    checks, artifacts = prove()
    event_composition.audit_tree(parents)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "audited_receipts": len(tree),
            "history_length": "unbounded",
            "scope": "Initialized unbounded induction of callback pre/post-access sampling versus a base-edge collector with the actual one-base-edge acknowledgement mask and accepted register-2 device resets. Reset withdraws the post-access line while preserving pending and same-edge CPU events, including abort retriggers. The actual reset establishment/quiet closure certificate and parent bus separation establish a quiet first base edge after reset; a proof monitor records that interval. Only this recovery edge is suppressed, after which callbacks are freely overapproximated. Accepted event windows agree; all other full-256-bit native sampler fields are preserved. Acceptance consumes only a collector marker and does not execute CPU instructions. No formal assumptions. Lost reset-edge events, clearing pending events on reset, and removing reset recovery require observational counterexamples.",
            "eligibility": "One accepted access cannot combine acknowledgement, command completion and register-2 reset. There must be an intervening base edge between accepted accesses; the actual parent epoch theorem proves a stronger fifteen-edge gap. Reset requests and abort causes are unrestricted and can coincide with accepted callbacks. Reset itself never invalidates eligibility. Callback proposals are free; the effective callback is quiet on the first base edge after reset as guaranteed by the audited actual controller proof. Only the collector marker starts clear; other source bits are arbitrary.",
            "limits": "Abstract base-edge event-window theorem, with actual reset quiet closure and shared-reset parent/source epoch recurrence recursively audited. Original reset dispatch is covered by its finite source projection; full original-device callback correspondence remains a premise. Source post-access and physical previous pins can temporarily differ. Complete instruction execution composition, IRQ generation, external warm resets, opcode eligibility and whole-game/board equivalence retain their separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Callback event-summary inputs changed")


if __name__ == "__main__":
    qualify()
