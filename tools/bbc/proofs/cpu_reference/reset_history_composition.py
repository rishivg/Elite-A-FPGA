"""Bind native source construction, profile, reset exit and official histories."""

from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import (
    aig_c,
    documented_model,
    foundation,
    frontend,
    reset_history_handoff,
    source_history,
    source_profile,
)
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-reset-history-composition"


def qualify():
    parents = [
        module.DIRECTORY / "result.json"
        for module in (foundation, source_profile, reset_history_handoff, source_history)
    ]
    reports = [memory_composition.sealed(path) for path in parents]
    files = [
        Path(__file__),
        *parents,
        *[
            Path(module.__file__)
            for module in (
                aig_c,
                documented_model,
                foundation,
                frontend,
                reset_history_handoff,
                source_history,
                source_profile,
                memory_composition,
            )
        ],
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
    ]
    before = hashes(files)
    if frontend.FIELDS != aig_c.FIELDS:
        raise ValueError("Source history uses a different packed C field interface")
    if (
        reports[1].get("native_constructor_profile") is not True
        or source_profile.USED_PINS != 0x5FFFFFFF
    ):
        raise ValueError("Reset/handoff lacks the native source profile")
    first = reset_history_handoff.DIRECTORY / "entry-prefix.v"
    second = source_history.DIRECTORY / "prefix.v"
    if digest(first) != digest(second):
        raise ValueError("Reset exit and history theorem have different constructors")
    if reports[3]["opcodes"] != documented_model.inventory() or reports[3]["seed_domain"] != {
        "seed_bits": 7,
        "seed_combinations": 128,
    }:
        raise ValueError("History theorem narrowed its opcode or reset seed domain")
    preservation = reports[3]["complete_source_preservation"]
    if preservation["passed"] is not True or len(preservation["checks"]) != 65:
        raise ValueError("Official history lacks complete-source preservation")
    for path in parents:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([first, second]),
            "argument": [
                "Native construction establishes the source profile. The complete-source transition theorem preserves it on defined transitions when incoming controls stay inside used pins.",
                "That profile supplies the reset-entry antecedents: decimal capability, cleared unused status and zero low IRQ history. The reset theorem then places the complete state, fetch request and retirement observations in the history entry after seven reset ticks.",
                "The first-step handoff compares state and pins with only unused pin bits omitted. The invariant makes those omitted live/saved bits zero on both sides, so the compared first-step states and pins are equal in full.",
                "Both the handoff and unbounded official-history theorem use byte-identical entry constructors and the same qualified C field interface. Matching state/pins and read-only observer history transfer the subsequent source bus/retirement property for admitted official instructions and arbitrary IRQ/NMI/response bytes.",
            ],
            "scope": "Conditional source reset-to-official-history composition from the native Model B profile. Parent proofs and exact constructors are checked; the argument uses their initialized/transition/interface theorems. Defined transitions, incoming used-pin controls and official fetched instructions are declared domains.",
            "limits": "Source composition only. Physical enable/event mapping, actual RTL continuous reset/interrupt composition, instruction eligibility and whole-machine/game equivalence remain unproved.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Source composition inputs changed")
    print("Source reset/history composition: PASS", flush=True)


if __name__ == "__main__":
    qualify()
