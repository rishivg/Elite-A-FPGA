"""Check source reset exit membership in the generalized fetch-entry family."""

import re
import shutil
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import fetch_seed, prepared, reset_sequence
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def contract(prefix, *, fault=None):
    # Use the actual abstract prefix's state constructor, rather than a second
    # manually packed copy. Free hidden fields may be chosen at this handoff;
    # architectural values and retained NMI come from independent reset rules.
    line = next(line for line in prefix.splitlines() if "referenceState<=" in line)
    entry = line.split("referenceState<=", 1)[1].split(";referencePins<=", 1)[0]
    values = {
        "hiddenIR": "16'd7",
        "base": "({vectorHigh,vectorLow}-16'd13)",
        "hiddenAD": "{8'd0,vectorLow}",
        "a": "initialState[207:200]",
        "x": "initialState[199:192]",
        "y": "initialState[191:184]",
        "s": "(initialState[183:176]-8'd3)",
        "p": "(initialState[175:168]|8'h14)",
        "hiddenPins": "savedPins",
        "seedPrevious": "previousNmi",
        "seedOlderIrq": "savedState[103:99]",
        "hiddenBranch": "{7'd0,initialPins[26] && !initialState[170]}",
        "seedNmi": "recentNmi",
    }
    if fault == "lost-retained-nmi":
        values["seedNmi"] = "1'b0"
    elif fault == "wrong-previous-nmi":
        values["seedPrevious"] = "!previousNmi"
    elif fault is not None:
        raise ValueError("Unknown handoff fault")
    entry = re.sub(r"\b(" + "|".join(values) + r")\b", lambda m: values[m[0]], entry)
    return replace_once(
        reset_sequence.SEQUENCE,
        "assign equivalent=stateRelation && (phase==7 || (executingContext && !trap));",
        "wire entryPrecondition=initialState[96:88]==0 && "
        "initialState[39:32]==1 && !initialState[173];\n"
        f"wire [255:0] entryState={entry};\n"
        "wire entryMember=phase!=7 || !entryPrecondition || savedState==entryState;\n"
        "assign equivalent=stateRelation && (phase==7 || (executingContext && !trap)) && entryMember;",
    )


def qualify():
    reset_path = ROOT / "build/bbc/cpu-c-reset-sequence/result.json"
    history_path = ROOT / "build/bbc/cpu-c-control-induction-investigation/result.json"
    reset, history = [memory_composition.sealed(p) for p in (reset_path, history_path)]
    model = Path("/tmp/fpgaleet-c-reset-sequence/source.v")
    prefix = Path("/tmp/fpgaleet-c-documented-memory/prefix.v")
    if reset["artifacts"].get(str(model)) != digest(model) or history["inputs"].get(
        str(prefix)
    ) != digest(prefix):
        raise ValueError("Changed reset decoder or abstract entry constructor")
    inputs = [
        Path(__file__),
        *[Path(m.__file__) for m in (fetch_seed, prepared, reset_sequence, memory_composition)],
        Path(__file__).with_name("warm_checks.py"),
        Path(__file__).parents[1] / "video_cursor.py",
        reset_path,
        history_path,
        model,
        prefix,
        Path(shutil.which("yosys")),
    ]
    before = hashes(inputs)
    work = Path("/tmp/fpgaleet-c-reset-fetch-handoff")
    work.mkdir(exist_ok=True)
    entry = work / "entry-prefix.v"
    entry.write_text(fetch_seed.prefix(prefix.read_text()))
    artifacts, checks = [entry], []
    for name, fault in (
        ("positive", None),
        ("lost-retained-nmi", "lost-retained-nmi"),
        ("wrong-previous-nmi", "wrong-previous-nmi"),
    ):
        case = work / name
        case.mkdir(exist_ok=True)
        property_file, graph, log, witness = [
            case / n for n in ("contract.v", "graph.json", "proof.log", "witness.json")
        ]
        property_file.write_text(contract(entry.read_text(), fault=fault))
        command = f"read_verilog -formal -sv {model} {property_file}; prep -top SourceResetSequence -flatten; check -assert; opt; memory_map; opt; write_json {graph}"
        cached = prepared.prepare(case, command, [model, property_file], [graph], timeout=600)
        accepted = reset_sequence.run_query(
            graph,
            "SourceResetSequence",
            log,
            witness,
            "equivalent" if fault is None else "faultDefined",
            sequence=8,
            positive=fault is None,
        )
        checks.append({"name": name, "accepted": accepted, "preparation_cache_reused": cached})
        artifacts += [property_file, graph, log, case / "prepared.json", case / "prepare.log"]
        if witness.exists():
            artifacts.append(witness)
        print("Reset fetch membership " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    for p in (reset_path, history_path):
        memory_composition.sealed(p)
    stable = before == hashes(inputs)
    passed = stable and len(checks) == 3 and all(c["accepted"] for c in checks)
    write_json(
        ROOT / "build/bbc/cpu-c-reset-fetch-membership/result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Seven actual source reset ticks produce a complete 256-bit state in the generalized fetch-entry constructor, conditional on the existing low-IRQ invariant, canonical BCD enable and cleared unused status bit. Preconditions are implication antecedents, not assumptions. Architectural values, reset IR/AD, the reset-entry IRQ branch sample and retained NMI follow independent reset rules; only free older IRQ bits and saved pins are selected from the resulting state. Two entry-mapping faults require entirely defined failing source histories.",
            "limits": "Source state membership only. This does not prove the generalized entry history theorem, map its retirement model at the handoff, normalize live fetch pins, or compose actual RTL request/enable, OS or whole-game histories.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Source reset fetch membership incomplete")


if __name__ == "__main__":
    qualify()
