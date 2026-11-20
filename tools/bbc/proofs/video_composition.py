"""Compose current integrated video datapath and memory scheduling premises."""

from pathlib import Path

from tools.bbc import build
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def qualify():
    names = (
        "ram-proof",
        "video-memory-response-deadline-proof",
        "peripheral-pin-binding-proof",
        "video32-register-contract",
        "video96-register-contract",
        "video32-palette-contract",
        "video-palette-contract",
        "video32-cursor-segment-contract",
        "video-cursor-segment-contract",
    )
    paths = {name: build.BBC / name / "result.json" for name in names}
    reports = {name: memory_composition.sealed(path) for name, path in paths.items()}
    parents = {
        1: build.BBC / "BbcMachine.v",
        3: build.BBC / "full-video/BbcMachine96.v",
    }
    for multiplier, parent in parents.items():
        name = str(parent.relative_to(ROOT))
        shared = (
            "ram-proof",
            "video-memory-response-deadline-proof",
            "peripheral-pin-binding-proof",
        )
        local = (
            "video32-register-contract" if multiplier == 1 else "video96-register-contract",
            "video32-palette-contract" if multiplier == 1 else "video-palette-contract",
            "video32-cursor-segment-contract"
            if multiplier == 1
            else "video-cursor-segment-contract",
        )
        for premise in (*shared, *local):
            bound = reports[premise]["inputs"].get(
                name, reports[premise]["inputs"].get(str(parent))
            )
            if bound != digest(parent):
                raise ValueError("Video premises bind different actual parents")
        for premise in local:
            if reports[premise]["clock_multiplier"] != multiplier:
                raise ValueError("Video premise clock mismatch")
    files = [
        Path(__file__),
        Path(memory_composition.__file__),
        Path(__file__).with_name("video_cursor.py"),
        *paths.values(),
        *parents.values(),
    ]
    before = hashes(files)
    for path in paths.values():
        memory_composition.sealed(path)
    stable = before == hashes(files)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "premises": {name: str(path.relative_to(ROOT)) for name, path in paths.items()},
        "derivation": [
            "The arbitrary watched-word RAM theorem supplies every requested video byte from the declared image and accepted CPU writes, with response retention across CPU requests. Its two parent proofs discharge request non-overlap and bind the actual video address/data wiring.",
            "The actual parent video deadline theorem supplies a preceding fetch and ready/captured response before every first emitted bitmap character pixel, retaining actual CPU scheduling and accepted ULA control writes. It discharges the response-timing obligation without assuming RAM output equality.",
            "Each integrated video register theorem supplies masked register state, read-only light-pen captures and readback for the actual write/pulse/address histories. Parent pin routing binds the light-pen pulse to the system VIA callback and preserves external event bank selection.",
            "Each integrated palette theorem supplies live control/palette state and exact bitmap decoding at the actual physical pixel index for all byte/control histories. The RAM byte and held response therefore satisfy its arbitrary memory-data boundary.",
            "Each integrated cursor theorem supplies segment index, held inversion and bitmap RGB inversion for the actual start/skew/visibility/advance histories and all pre-cursor colours. Register/control values and decoded colours satisfy its free boundaries; no CRTC timing equivalence is inferred from them.",
        ],
        "scope": "Same-source composition of current actual-parent RAM, response scheduling, register bus, palette decoding, cursor inversion and peripheral pin-routing contracts at both rates. Every premise and artifact seal is checked before and after composition. This supplies local bitmap datapath semantics evaluated at actual CRTC/pixel/event histories.",
        "limits": "Declared simulation reset/RAM image. Independent CRTC counter/blink/raster/start histories and their absolute phase alignment, blanking and display placement, teletext/NULA, CPU/device algorithm composition and universal game equivalence remain unresolved. This is a partial video composition, not a whole-video or game theorem.",
        "universal_game_equivalence": False,
    }
    write_json(build.BBC / "video-datapath-composition/result.json", report)
    if not stable:
        raise ValueError("Video composition inputs changed")
    return report


if __name__ == "__main__":
    qualify()
