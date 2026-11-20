"""Prove the IC32 comparator cleanup from declared reset for arbitrary inputs."""

import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, write_json

from . import baselines, build
from .probe import device_rtl

BASELINE = "4abd5fa8576553ccb2cc61a136bbecee090c72d3"


def observed_module(text):
    """Expose the shared previous low nibble without altering any driver."""
    end = text.index(");")
    return (
        text[:end]
        + ",\n output wire [3:0] proofPrevious\n"
        + text[end : text.rindex("endmodule")]
        + "assign proofPrevious = previousPortB[3:0];\nendmodule\n"
    )


def miter(golden, candidate):
    """Compare every interface output, strengthened by proved latch invariants."""

    def ports(text):
        return re.findall(
            r"\b(input|output)\s+wire\s*(\[[^\]]+\])?\s*(\w+)\s*[,\n]",
            text[: text.index(");")],
        )

    declared = ports(golden)
    if (
        declared != ports(candidate)
        or not declared
        or ("output", "[7:0]", "io_latch")
        not in [(direction, width.replace(" ", ""), name) for direction, width, name in declared]
    ):
        raise ValueError("System-pin proof requires identical complete interfaces and latch output")
    inputs = [(width, name) for direction, width, name in declared if direction == "input"]
    outputs = [(width, name) for direction, width, name in declared if direction == "output"]
    outputs.append(("[3:0]", "proofPrevious"))
    wrapper = (
        "module PinsReachable("
        + ",".join("input wire " + width + " " + name for width, name in inputs)
        + ", output wire equivalent, goldenInvariant, candidateInvariant);\n"
    )
    for width, name in outputs:
        wrapper += "wire " + width + " g_" + name + ", c_" + name + ";\n"
    for module, prefix, label in (
        ("BbcSystemPinsBaseline", "g", "golden"),
        ("BbcSystemPinsCandidate", "c", "candidate"),
    ):
        connections = ["." + name + "(" + name + ")" for _, name in inputs]
        connections += ["." + name + "(" + prefix + "_" + name + ")" for _, name in outputs]
        wrapper += module + " " + label + "(" + ",".join(connections) + ");\n"
    wrapper += (
        "assign equivalent = "
        + " && ".join("(g_" + name + " == c_" + name + ")" for _, name in outputs)
        + ";\n"
    )
    wrapper += (
        "assign goldenInvariant = g_io_latch[g_proofPrevious[2:0]] == g_proofPrevious[3];\n"
        "assign candidateInvariant = c_io_latch[c_proofPrevious[2:0]] == c_proofPrevious[3];\n"
        "endmodule\n"
    )
    return observed_module(golden) + observed_module(candidate) + wrapper, outputs


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the current machine before binding the system-pin proof")
    directory = build.BBC / "system-pins-proof"
    sources = directory / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    original = baselines.historical(BASELINE, "rtl/bbc/BbcSystemPins.scala")
    replacements = {
        "RegNext(io.busPortB) init (0)": "RegNext(io.busPortB(3 downto 0)) init (0)",
        "when(io.busPortB =/= previousPortB)": "when(io.busPortB(3 downto 0) =/= previousPortB)",
    }
    candidate = original
    for old, new in replacements.items():
        if candidate.count(old) != 1:
            raise ValueError("Unrecognized baseline system-pin implementation")
        candidate = candidate.replace(old, new)
    for name, text in (("Baseline", original), ("Candidate", candidate)):
        (sources / (name + ".scala")).write_text(
            text.replace("case class BbcSystemPins()", "case class BbcSystemPins" + name + "()")
        )
    (sources / "GeneratePinsProof.scala").write_text(
        "package leet.bbc\nimport spinal.core._\nobject GeneratePinsProof extends App {\n"
        ' val config = SpinalConfig(targetDirectory = "'
        + str(directory / "generated")
        + '", defaultConfigForClockDomains=ClockDomainConfig(resetActiveLevel=HIGH))\n'
        " config.generateVerilog(BbcSystemPinsBaseline())\n"
        " config.generateVerilog(BbcSystemPinsCandidate())\n}\n"
    )
    dependencies = [
        *baselines.dependencies(BASELINE),
        Path(__file__),
        ROOT / "rtl/bbc/BbcSystemPins.scala",
        *sources.glob("*.scala"),
    ]
    before = build.hashes(dependencies)
    with (directory / "generate.log").open("w") as log:
        subprocess.run(
            [
                os.environ.get("SBT", "sbt"),
                f'set Compile / unmanagedSourceDirectories += file("{sources}")',
                "runMain leet.bbc.GeneratePinsProof",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
    generated = directory / "generated"
    golden = (generated / "BbcSystemPinsBaseline.v").read_text()
    candidate = (generated / "BbcSystemPinsCandidate.v").read_text()
    text, outputs = miter(golden, candidate)
    rtl = directory / "reachable-miter.v"
    rtl.write_text(text)
    wrong = "(io_busPortB[3 : 0] != previousPortB)"
    if text.count(wrong) != 1:
        raise ValueError("Missing candidate value-bit comparison for negative control")
    negative = directory / "wrong-value-bit.v"
    negative.write_text(text.replace(wrong, "(io_busPortB[2 : 0] != previousPortB[2 : 0])"))

    def prove(source, label):
        command = (
            f"read_verilog {source}; prep -top PinsReachable -flatten; async2sync; opt_clean; "
            "sat -set-init-zero -tempinduct -seq 3 -maxsteps 12 -prove equivalent 1 "
            "-prove goldenInvariant 1 -prove candidateInvariant 1 -verify -timeout 60"
        )
        result = subprocess.run(
            ["yosys", "-Q", "-T", "-p", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
        )
        (directory / (label + ".log")).write_text(result.stdout + result.stderr)
        return result

    positive = prove(rtl, "proof")
    rejected = prove(negative, "negative")
    detected = rejected.returncode != 0 and "proof did fail" in rejected.stderr
    unchanged = before == build.hashes(dependencies) and build.generation_current()
    production = device_rtl(["BbcSystemPins"])
    actual_body = re.search(r"^module\s+BbcSystemPins\b[\s\S]*?^endmodule\b", production, re.M)[0]
    candidate_body = re.search(
        r"^module\s+BbcSystemPinsCandidate\b[\s\S]*?^endmodule\b", candidate, re.M
    )[0].replace("BbcSystemPinsCandidate", "BbcSystemPins")
    bound = actual_body == candidate_body
    report = {
        "passed": positive.returncode == 0 and detected and unchanged,
        "production_bound": bound,
        "qualified_production_change": bound
        and positive.returncode == 0
        and detected
        and unchanged,
        "inputs": before,
        "generated": build.hashes([*generated.glob("*.v"), rtl, negative]),
        "baseline_commit": BASELINE,
        "compared_outputs": outputs,
        "negative_control_detected": detected,
        "inputs_unchanged": unchanged,
        "scope": "Temporal induction from the declared zero reset values. Every original output and shared previous low nibble agrees for arbitrary input histories. Selected-latch invariants are proved jointly, rather than assumed. No output exclusions or clock-edge omissions.",
        "limits": "Component preservation, not original-game equivalence or a state-archive converter. A private candidate is not an active production change unless production_bound is true.",
    }
    write_json(directory / "result.json", report)
    print(
        f"System-pin comparator preservation: {report['passed']}; production bound: {bound}",
        flush=True,
    )
    if not report["passed"]:
        raise ValueError("System-pin preservation proof failed")
    return report


if __name__ == "__main__":
    qualify()
