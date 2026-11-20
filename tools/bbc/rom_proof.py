"""Prove the actual synchronous ROM port and verify every initialized byte."""

import os
import re
import shutil
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json
from tools.validation.setup import verify

from . import build, bus_binding_proof, prepare, video96


def initial_image(parent, directory, expected):
    names = re.findall(r'\$readmemb\("([^\"]+)",rom\);', parent)
    if len(names) != 1:
        raise ValueError("ROM proof requires one declared initial image")
    image = (directory / names[0]).resolve()
    if not image.is_relative_to(directory.resolve()):
        raise ValueError("ROM image escapes its generated directory")
    words = image.read_text().split()
    if words != [f"{byte:08b}" for byte in expected]:
        raise ValueError("Generated ROM differs from the verified OS/BASIC/DFS bytes")
    return image, names[0]


def observed(text, top, image_name):
    parent = bus_binding_proof.module(text, top)
    for pattern in (
        r"reg\s+\[7:0\]\s+rom\s*\[0:49151\]\s*;",
        r"reg\s+\[7:0\]\s+rom_spinal_port0\s*;",
        r"wire\s+\[7:0\]\s+romData\s*;",
        r"assign\s+romData\s*=\s*rom_spinal_port0\s*;",
    ):
        if len(re.findall(pattern, parent)) != 1:
            raise ValueError("Changed actual ROM observation binding")
    # Every array word is read-only. Initialization is checked in full above;
    # only unrelated initial words are relaxed in the inductive port proof.
    if re.search(r"\brom\s*\[[^\]]+\]\s*(?:<=|=(?!=))", parent):
        raise ValueError("ROM is no longer read-only")
    initializer = '$readmemb("' + image_name + '",rom);'
    if parent.count(initializer) != 1:
        raise ValueError("Missing exact ROM initialization abstraction target")
    addition = """
(* anyconst *) reg [15:0] watched;
(* anyconst *) reg [7:0] expectedWord;
reg validResponse=0;
initial begin
 assume(watched<49152);
 assume(rom[watched]==expectedWord);
end
always @(posedge clk) if(cpuEnable) validResponse <= romAddress==watched;
always @* begin
 assert(rom[watched]==expectedWord);
 if(validResponse) assert(romData==expectedWord);
end
"""
    instrumented = parent.replace(initializer, "// Exact watched initial word retained below.")
    instrumented = instrumented.removesuffix("endmodule") + addition + "endmodule"
    return text.replace(parent, instrumented, 1)


def qualify(*, sources=None, directory=None):
    supplied = sources is not None
    if not supplied:
        prepare.main()
        if not build.generation_current():
            raise ValueError("Generate the current whole machine before proving ROM")
        generation96 = video96.generate()
    else:
        verify()
    sources = sources or [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    emulator = source_root("jsbeeb")
    roms = [emulator / "public/roms" / name for name in ("os.rom", "BASIC.ROM", "b/DFS-1.2.rom")]
    expected = b"".join(path.read_bytes() for path in roms)
    if len(expected) != 49152:
        raise ValueError("Wrong declared ROM size")
    images = [
        initial_image(bus_binding_proof.module(source.read_text(), top), source.parent, expected)
        for _, source, top in sources
    ]
    smtbmc = shutil.which("yosys-smtbmc")
    z3 = shutil.which("z3") or str(build.BBC / "formal-tools/bin/z3")
    if not smtbmc or not Path(z3).is_file():
        raise ValueError("ROM induction requires yosys-smtbmc and Z3")
    data = Path(
        os.environ.get("YOSYS_DATDIR", str(Path(smtbmc).resolve().parent.parent / "share/yosys"))
    )
    if not (data / "smtio.py").is_file():
        raise ValueError("Set YOSYS_DATDIR to the installed Yosys Python-helper directory")
    environment = {
        **os.environ,
        "PATH": str(Path(z3).parent) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(data),
    }
    dependencies = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(prepare.__file__),
        ROOT / "tools/validation/reference.lock.json",
        ROOT / "rtl/bbc/BbcCpu.v",
        *roms,
        *[source for _, source, _ in sources],
        *[image for image, _ in images],
        Path(smtbmc),
        Path(z3),
        data / "smtio.py",
    ]

    def inputs():
        return {str(path): digest(path) for path in dependencies}

    before = inputs()
    directory = directory or build.BBC / "rom-proof"
    directory.mkdir(exist_ok=True)

    def execute(command, name, cwd=ROOT):
        result = subprocess.run(
            command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=120
        )
        (directory / (name + ".log")).write_text(result.stdout + result.stderr)
        return result

    clocks = []
    for (multiplier, source, top), (_, image_name) in zip(sources, images, strict=True):
        original = source.read_text()
        parent = bus_binding_proof.module(original, top)
        children = re.findall(r"^\s+\w+\s+(\w+)\s*\(\s*$", parent, re.M)

        def compile_model(text, name):
            model = directory / f"{name}{multiplier}.v"
            smt = model.with_suffix(".smt2")
            model.write_text(observed(text, top, image_name))
            command = (
                f"read_verilog -formal -sv {model} {ROOT / 'rtl/bbc/BbcCpu.v'}; hierarchy -top {top}; "
                + "delete "
                + " ".join(f"{top}/c:{child}" for child in children)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:*; "
                + f"cutpoint {top}/w:cpuEnable {top}/w:romAddress; "
                + "opt_clean -purge; flatten; async2sync; chformal -lower; dffunmap; "
                + f"opt_clean; memory_collect; write_smt2 -wires {smt}"
            )
            execute(
                ["yosys", "-Q", "-T", "-p", command],
                name + str(multiplier) + "-compile",
                source.parent,
            ).check_returncode()
            return smt

        def solve(smt, name, induction=False):
            result = execute(
                [
                    smtbmc,
                    "-s",
                    "z3",
                    *(["-i"] if induction else []),
                    "-t",
                    "4",
                    "--dump-vcd",
                    str(directory / (name + ".vcd")),
                    str(smt),
                ],
                name,
            )
            return result.returncode == 0 and "Status: PASSED" in result.stdout, result

        smt = compile_model(original, "actual")
        bounded, _ = solve(smt, "bounded" + str(multiplier))
        inductive, _ = solve(smt, "induction" + str(multiplier), True)
        negatives = []
        for name, correct, wrong in (
            (
                "wrong-hold",
                "if(cpuEnable) begin\n      rom_spinal_port0",
                "if(1'b1) begin\n      rom_spinal_port0",
            ),
            (
                "wrong-address",
                "rom_spinal_port0 <= rom[romAddress];",
                "rom_spinal_port0 <= rom[romAddress ^ 16'h0001];",
            ),
        ):
            if original.count(correct) != 1:
                raise ValueError("Missing actual ROM mutation target: " + name)
            bad_smt = compile_model(original.replace(correct, wrong), name)
            _, result = solve(bad_smt, name + str(multiplier))
            negatives.append(
                {
                    "name": name,
                    "detected": result.returncode != 0 and "Status: FAILED" in result.stdout,
                }
            )
        clocks.append(
            {
                "clock_multiplier": multiplier,
                "bounded_passed": bounded,
                "induction_passed": inductive,
                "negative_controls": negatives,
            }
        )
        print(f"Actual ROM port {multiplier}: bounded={bounded}, inductive={inductive}", flush=True)
    stable = before == inputs() and (
        supplied or (build.generation_current() and generation96 == video96.snapshot())
    )
    report = {
        "passed": stable
        and all(
            c["bounded_passed"]
            and c["induction_passed"]
            and all(n["detected"] for n in c["negative_controls"])
            for c in clocks
        ),
        "inputs": before,
        "inputs_unchanged": stable,
        "root_generation_bound": not supplied,
        "artifacts": build.hashes(
            p for p in directory.iterdir() if p.suffix in {".v", ".smt2", ".vcd", ".log"}
        ),
        "clocks": clocks,
        "initialized_bytes_verified": len(expected),
        "scope": "Both actual parent ROM ports, unchanged functional drivers. Every one of 49,152 generated initialization bytes equals the verified original OS/BASIC/DFS concatenation. A fixed arbitrary watched address and byte prove synchronous accepted read capture and response retention inductively for every possible initialized word. The array is read-only; unrelated initial words are relaxed. Wrong hold and address mutations must fail at both rates.",
        "limits": "Local digital ROM contract. Accepted enable and ROM address are cut inputs; separate parent memory-routing and clock proofs cover their production connections. Initial watched-word equality encodes the checked fixture, not any subsequent output equality. No universal CPU or game claim.",
    }
    write_json(directory / "result.json", report)
    if not report["passed"]:
        raise ValueError("Actual ROM proof failed; models and counterexamples retained")
    return report


if __name__ == "__main__":
    qualify()
