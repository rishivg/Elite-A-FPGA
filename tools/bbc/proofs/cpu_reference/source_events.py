"""Qualify original within-callback NMI capture through the production adapter."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc import cycle_reference
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import nmi_sampling
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/source-event-capture"
ADAPTER = ROOT / "tools/bbc/reference_events.mjs"


def outcome(trial, mode, positive):
    output = trial.stdout
    if mode == "abort":
        prefix = "elite.ssd loaded as 80 track: its catalogue claims 800 sectors\n"
        if not output.startswith(prefix):
            return False
        output = output.removeprefix(prefix)
    if not positive:
        return (
            trial.returncode == 1
            and not output.strip()
            and "AssertionError [ERR_ASSERTION]: ABORT_RETRIGGER_LOST" in trial.stderr
            and "  actual: 1," in trial.stderr.splitlines()
            and "  expected: 2," in trial.stderr.splitlines()
        )
    try:
        result = json.loads(output)
    except ValueError:
        return False
    if not isinstance(result, dict):
        return False
    return (
        trial.returncode == 0
        and not trial.stderr
        and result.get("passed") is True
        and (
            result.get("cases") == 96
            if mode == "domain"
            else result.get("restored_levels") == 2
            and result.get("restored_pending") is True
            and result.get("handlers") == 9
            if mode == "lifecycle"
            else result.get("handlers") == 2
            and result.get("status") == 28
            and result.get("result") == 10
            and len(result.get("transitions", [])) == 4
        )
    )


def qualify():
    if not cycle_reference.current():
        raise ValueError("Qualify the independent cycle CPU before event capture")
    parent = nmi_sampling.DIRECTORY / "result.json"
    memory_composition.sealed(parent)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = Path(__file__).with_suffix(".mjs")
    production = ROOT / "tools/bbc/reference.mjs"
    if production.read_text().count('import { cycleCpu } from "./reference_events.mjs";') != 1:
        raise ValueError("Production reference bypasses original NMI transition capture")
    files = [
        Path(__file__),
        helper,
        ADAPTER,
        production,
        ROOT / "tools/bbc/reference_cycle.mjs",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "build/bbc/disc.ssd",
        cycle_reference.BINARY,
        cycle_reference.RECEIPT,
        Path(cycle_reference.__file__),
        Path(memory_composition.__file__),
        Path(nmi_sampling.__file__),
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        parent,
        *sorted((reference / "src").rglob("*.js")),
        Path(shutil.which("node")),
    ]
    before = hashes(files)
    drop_low = DIRECTORY / "drop-low.mjs"
    mutated = replace_once(
        ADAPTER.read_text(),
        '"./reference_cycle.mjs"',
        json.dumps((ROOT / "tools/bbc/reference_cycle.mjs").as_uri()),
    )
    drop_low.write_text(
        replace_once(mutated, "if (before !== after) sample(after);", "if (after) sample(after);")
    )
    artifacts, checks = [drop_low], []
    for name, mode, adapter, positive in (
        ("setter-closure", "domain", ADAPTER, True),
        ("native-lifecycle", "lifecycle", ADAPTER, True),
        ("original-late-dma", "abort", ADAPTER, True),
        ("post-callback-only", "abort", ROOT / "tools/bbc/reference_cycle.mjs", False),
        ("lost-within-callback-low", "abort", drop_low, False),
    ):
        trial = subprocess.run(
            [
                "node",
                str(helper),
                str(reference),
                str(adapter),
                str(cycle_reference.BINARY),
                str(ROOT / "tools/bbc/reference_drive.mjs"),
                str(ROOT / "build/bbc/disc.ssd"),
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        log = DIRECTORY / f"{name}.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = outcome(trial, mode, positive)
        checks.append({"name": name, "accepted": accepted, "positive": positive})
        artifacts.append(log)
        print(f"Original event capture {name}: " + ("PASS" if accepted else "FAIL"), flush=True)
        if not accepted:
            raise ValueError("Original event capture incomplete; inspect " + str(log))
    memory_composition.sealed(parent)
    stable = before == hashes(files) and cycle_reference.current()
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "scope": "Production cycle reference observes every merged-line transition through the original CPU source setter, including changes inside scheduler and bus callbacks. Exhaustive 96-case closed setter domain preserves original mask, edge latch, receiver and return value. Executing native C firmware checks merged-source masking, own high/low snapshot continuation and reset-withdrawn retained events. Original public ready READ with unacknowledged first byte produces two NMI handlers, including the late-DMA abort low/high in one callback. Two faulty adapters must reach that exact missing-handler assertion. Sealed all-state C sampler evidence supplies capture semantics without advancing instruction or pipeline age.",
            "limits": "Setter closure applies to the three original declared NMI sources; direct mutation of source masks is outside event observation, except own restore followed by native snapshot restoration. Native regressions are finite declared firmware histories, not a full CPU or FDC equivalence proof. RTL event correspondence, abort/retry timing, arbitrary warm reset and whole-game/board equivalence remain open. This producer change invalidates current-reference captures; archived original evidence retains its historical identity.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Original event capture inputs changed")


if __name__ == "__main__":
    qualify()
