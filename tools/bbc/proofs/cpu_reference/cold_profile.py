"""Prove quiet reference entry eligibility after the real cold prefix."""

import json
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import cold_entry, frontend, instructions
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def qualify():
    premise = ROOT / "build/bbc/cpu-c-cold-entry-bridge/result.json"
    memory_composition.sealed(premise)
    old = Path("/tmp/fpgaleet-c-cold-entry")
    model, wrapper, original_contract = [
        old / name for name in ("positive/source.v", "prefix.v", "contract.v")
    ]
    source = ROOT / "build/bbc/reference-cycle/m6502.h"
    work = Path("/tmp/fpgaleet-c-cold-profile")
    work.mkdir(exist_ok=True)
    directory = ROOT / "build/bbc/cpu-c-cold-entry-profile"
    directory.mkdir(exist_ok=True)
    files = [
        Path(__file__),
        Path(cold_entry.__file__),
        Path(frontend.__file__),
        Path(instructions.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        premise,
        model,
        wrapper,
        original_contract,
        source,
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    fields = instructions.fields("state")
    quiet = " && ".join(
        f"{fields[name]}=={width}'d{1 if name == 'bcd_enabled' else 0}"
        for name, width in frontend.FIELDS
        if name
        in {
            "irq_pip",
            "nmi_pip",
            "brk_flags",
            "bcd_enabled",
            "io_ddr",
            "io_out",
            "io_inp",
            "io_pins",
        }
    )
    profile = (
        f"{quiet} && ({fields['P']} & 8'h30)==8'h10 && "
        f"{fields['branch_irq']}==0 && {fields['PINS']}==pins && "
        f"(pins & ~64'hffffff)==64'h3000000 && pins[15:0]=={fields['PC']}"
    )
    contract = work / "contract.v"
    text = replace_once(
        original_contract.read_text(),
        "flags==((p&8'hcf)|8'h10) && pins[25:24]==2'b11",
        "flags==((p&8'hcf)|8'h10) && pins[25:24]==2'b11 && eligibleProfile",
    )
    contract.write_text(
        replace_once(text, "endmodule", f"wire eligibleProfile={profile};\nendmodule")
    )
    target = "case (0x28<<3)|3: c->P=(_GD()|M6502_BF)&~M6502_XF;_FETCH();break;"
    variants = [("positive", model)]
    artifacts = [contract]
    for name, fault in (
        ("lost-bcd-profile", "c->bcd_enabled=0;"),
        ("spurious-interrupt-pipeline", "c->irq_pip=1;"),
    ):
        case = work / name
        case.mkdir(exist_ok=True)
        reference, rtl = case / "reference.h", case / "source.v"
        reference.write_text(
            replace_once(
                source.read_text(), target, target.replace("_FETCH();", fault + "_FETCH();")
            )
        )
        expanded = subprocess.check_output(
            ["gcc", "-E", "-P", "-"], input=frontend.extract(reference), text=True
        )
        rtl.write_text(instructions.specialized(expanded, [0, 0xA2, 0x9A, 0xA9, 0x48, 0xA0, 0x28]))
        variants.append((name, rtl))
        artifacts.extend([reference, rtl])
    results = []
    for name, rtl in variants:
        case = work / name
        case.mkdir(exist_ok=True)
        log, graph, witness = [case / n for n in ("proof.log", "graph.json", "witness.json")]
        witness.unlink(missing_ok=True)
        mode = "-tempinduct -initsteps 36 -maxsteps 40" if name == "positive" else "-seq 38"
        command = (
            f"read_verilog -formal -sv {rtl}; read_verilog -formal {wrapper} {contract}; "
            f"prep -top CColdEntry -flatten; check -assert; opt; async2sync; opt_clean; write_json {graph}; "
            f"sat -set-init-zero {mode} -prove equivalent 1 -verify -timeout 120 "
            f"-show equivalent -show prefix.age -dump_json {witness}"
        )
        with log.open("w") as output:
            run = subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        if any(
            c["type"] == "$assume"
            for c in json.loads(graph.read_text())["modules"]["CColdEntry"]["cells"].values()
        ):
            raise ValueError("Cold profile must have no assumptions")
        accepted = (
            run.returncode == 0 and "Induction step proven: SUCCESS!" in log.read_text()
            if name == "positive"
            else run.returncode != 0
            and "ERROR: Called with -verify and proof did fail!" in log.read_text()
            and witness.is_file()
            and any(
                s["name"] == "equivalent" and "0" in s.get("wave", "")
                for s in json.loads(witness.read_text())["signal"]
            )
        )
        artifacts.extend([log, graph, *([witness] if witness.exists() else [])])
        results.append({"name": name, "accepted": accepted})
        print(name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    stable = before == hashes(files)
    passed = stable and len(results) == 3 and all(r["accepted"] for r in results)
    write_json(
        directory / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "proofs": results,
            "scope": "The exact real source reset/register/PLP prefix from the sealed cold-entry premise, strengthened with quiet interrupt, BCD, 6510 and status profile. Full internal prior-pin equality, zero branch latch and opcode-fetch PC/RW/SYNC validity are checked at its final boundary. No assumptions or internal state assignments beyond original native reset initialization.",
            "limits": "Source C translator remains a trust boundary. Quiet cold entry followed by frozen reference state, not arbitrary opcode streams, interrupts, warm reset or a whole-game proof. Two real source mutations require initialized eligibility counterexamples. Successor composition remains separate.",
            "universal_game_equivalence": False,
        },
    )
    print("Real cold entry eligibility: " + ("PASS" if passed else "FAIL"), flush=True)
    if not passed:
        raise ValueError("Cold profile proof incomplete")


if __name__ == "__main__":
    qualify()
