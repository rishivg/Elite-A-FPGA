"""Compose current RAM/ROM contents, parent routing and response deadlines."""

import json
from pathlib import Path

from tools.bbc import build
from tools.validation.common import ROOT, digest, write_json


def sealed(path):
    report = json.loads(path.read_text())
    if report.get("passed") is not True or report.get("inputs_unchanged") is not True:
        raise ValueError("Incomplete memory premise: " + str(path))
    for group in ("inputs", "artifacts"):
        if not report.get(group):
            raise ValueError("Unsealed memory premise: " + group)
        for name, expected in report[group].items():
            source = Path(name) if Path(name).is_absolute() else ROOT / name
            if not source.is_file() or digest(source) != expected:
                raise ValueError("Changed memory premise: " + name)
    return report


def qualify():
    names = (
        "ram-proof",
        "rom-proof",
        "memory-binding-proof",
        "cpu-request-deadline-proof",
        "memory-response-deadline-proof",
    )
    paths = {name: build.BBC / name / "result.json" for name in names}
    premises = {name: sealed(path) for name, path in paths.items()}
    parents = ("build/bbc/BbcMachine.v", "build/bbc/full-video/BbcMachine96.v")
    for name, report in premises.items():
        for source in (*parents, "rtl/bbc/BbcCpu.v"):
            # The two scheduler cones do not use CPU implementation contents.
            if source == "rtl/bbc/BbcCpu.v" and name.endswith("deadline-proof"):
                continue
            expected = digest(ROOT / source)
            bound = report["inputs"].get(source, report["inputs"].get(str(ROOT / source)))
            if bound != expected:
                raise ValueError("Memory premises use different actual RTL: " + source)
    ram, rom = premises["ram-proof"], premises["rom-proof"]
    if not (
        ram["bounded_passed"]
        and ram["induction_passed"]
        and ram["negative_control_detected"]
        and {p["machine"] for p in ram["parent_bindings"]} == {"BbcMachine", "BbcMachine96"}
        and all(p["passed"] for p in ram["parent_bindings"])
        and rom["initialized_bytes_verified"] == 49152
        and {p["clock_multiplier"] for p in rom["clocks"]} == {1, 3}
        and all(p["induction_passed"] for p in rom["clocks"])
    ):
        raise ValueError("Incomplete memory or parent domain")
    spacing = premises["cpu-request-deadline-proof"]["clocks"]
    if {p["clock_multiplier"]: p["minimum_master_edges_between_acceptances"] for p in spacing} != {
        1: 16,
        3: 48,
    }:
        raise ValueError("Changed parent scheduling domain")
    files = [Path(__file__), *paths.values(), *[ROOT / p for p in parents]]
    before = build.hashes(files)
    for path in paths.values():
        sealed(path)
    stable = before == build.hashes(files)
    directory = build.BBC / "memory-response-composition"
    directory.mkdir(exist_ok=True)
    report = {
        "passed": stable,
        "inputs": before,
        "inputs_unchanged": stable,
        "premises": {name: str(path.relative_to(ROOT)) for name, path in paths.items()},
        "clocks": [1, 3],
        "derivation": [
            "Actual parent wiring and phase logic discharge the RAM theorem's non-overlapping CPU/video request premise at both rates.",
            "For any accepted RAM read address, the watched-word induction supplies that word's value at the request, its synchronous read result, subsequent CPU response capture and retention through video accesses. Universality of the watched address covers all 32,768 words; no memory snapshot is copied into the DUT.",
            "The actual RAM response-capture guard and both actual CPU schedulers prove that capture completes before the next acceptance consuming the previous RAM response. The separate spacing theorem establishes at least 16/48 master edges between CPU acceptances.",
            "The ROM theorem verifies every original OS/BASIC/DFS byte and proves accepted synchronous read capture and retention for every address. Parent routing supplies the same actual enable, bank/address/presence and held selection; the next acceptance therefore consumes the preceding accepted ROM response.",
            "Registered parent selection routes the completed, retained RAM or ROM byte into the actual CPU read_data port. Peripheral selection instead returns the previously accepted peripheral byte, whose device semantics remain an independent obligation.",
        ],
        "scope": "Current-source composition for accepted RAM and ROM read responses at both actual master rates, including video arbitration and response deadlines. All premise inputs and artifacts are rechecked; no new SAT input assumption, finite gameplay inference or DUT state injection.",
        "limits": "Declared RAM/ROM initialization and initial reset. RAM CPU readback during writes is unspecified by the RAM premise. Peripheral semantics, Original phase alignment, undocumented instruction reachability, arbitrary subsequent cold resets and universal whole-game equivalence remain separate.",
        "universal_game_equivalence": False,
    }
    write_json(directory / "result.json", report)
    if not stable:
        raise ValueError("Memory composition inputs changed")
    return report


if __name__ == "__main__":
    qualify()
