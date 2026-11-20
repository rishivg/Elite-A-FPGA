"""Prove full structural-adapter dead-wire pruning for every input bit."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import prune
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, digest, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-structural-adapter/result.json"
    saved = memory_composition.sealed(premise)
    source = Path("/tmp/fpgaleet-c-structural-reference/full.v")
    if saved["artifacts"].get(str(source)) != digest(source):
        raise ValueError("Changed full structural C adapter")
    work = Path("/tmp/fpgaleet-c-structural-pruning")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-structural-pruning"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(prune.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        source,
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    pruned, counts = prune.prune(source.read_text())
    raw = work / "pruned.v"
    raw.write_text(pruned)
    original = work / "original-miter.v"
    original.write_text(
        replace_once(source.read_text(), "module M6502ReferenceNext(", "module OriginalReference(")
    )
    reduced = work / "pruned-miter.v"
    reduced.write_text(
        replace_once(pruned, "module M6502ReferenceNext(", "module PrunedReference(")
    )
    contract = work / "contract.v"
    contract.write_text("""module PruningEquality(input [255:0] state,input [63:0] pins,output equivalent);
wire [255:0] a,b;wire [63:0] x,y;wire u,v;
OriginalReference original(.state_in(state),.pins_in(pins),.state_out(a),.pins_out(x),.trap(u));
PrunedReference pruned(.state_in(state),.pins_in(pins),.state_out(b),.pins_out(y),.trap(v));
assign equivalent=a==b && x==y && u==v;
endmodule
""")
    graph = work / "graph.json"
    preparation = work / "prepare.log"
    cache = work / "prepared-receipt.json"
    command = f"read_verilog -formal -sv {original} {reduced} {contract}; prep -top PruningEquality -flatten; check -assert; opt; write_json {graph}"
    cache_inputs = {
        "command": command,
        "files": hashes([original, reduced, contract, Path(shutil.which("yosys"))]),
    }
    recorded = json.loads(cache.read_text()) if cache.exists() else {}
    if not (
        recorded.get("inputs") == cache_inputs
        and graph.is_file()
        and recorded.get("graph_sha256") == digest(graph)
        and preparation.is_file()
        and recorded.get("log_sha256") == digest(preparation)
    ):
        with preparation.open("w") as output:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=600,
            )
        write_json(
            cache,
            {
                "inputs": cache_inputs,
                "graph_sha256": digest(graph),
                "log_sha256": digest(preparation),
            },
        )
    module = json.loads(graph.read_text())["modules"]["PruningEquality"]
    if any(cell["type"] == "$assume" for cell in module["cells"].values()):
        raise ValueError("Pruning property acquired an input assumption")
    log = work / "proof.log"
    command = f"read_json {graph}; hierarchy -top PruningEquality; check -assert; sat -prove equivalent 1 -verify -timeout 120 -show equivalent"
    with log.open("w") as output:
        run = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    passed = (
        run.returncode == 0 and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
    )
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": passed and stable,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes([p for p in work.iterdir() if p.is_file()]),
            **counts,
            "scope": "Exact full structural-adapter output equality after deleting only wire definitions outside the dependency cones of all 256 output state bits, all 64 output pin bits and trap. Every 256-bit input state and 64-bit pin combination is admitted; no input assumptions or state overrides.",
            "limits": "Preservation of the structural adapter, not proof of complete C translation correctness or CPU/game equivalence. The native/procedural structural-validation premise retains its own bounds.",
            "universal_game_equivalence": False,
        },
    )
    if not passed or not stable:
        raise ValueError("Full structural-adapter pruning proof failed")
    print("Full structural pruning: PASS; " + str(counts), flush=True)


if __name__ == "__main__":
    qualify()
