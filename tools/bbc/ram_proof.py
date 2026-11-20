"""Prove shared-RAM CPU/video read retention and actual phase separation."""

import os
import re
import shutil
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json

from . import build, video96
from .probe import device_rtl


def initial_image(module, directory):
    names = re.findall(r'\$readmemb\("([^\"]+)",memory\);', module)
    if len(names) != 1:
        raise ValueError("RAM proof requires one declared initial image")
    image = (directory / names[0]).resolve()
    if not image.is_relative_to(directory.resolve()):
        raise ValueError("RAM image escapes its generated directory")
    words = image.read_text().split()
    if words != ["00000000"] * 256 + ["11111111"] * (32768 - 256):
        raise ValueError("RAM initial image differs from the declared BBC power-on profile")
    return image, names[0]


def contract(module, image_name):
    initializer = '$readmemb("' + image_name + '",memory);'
    if module.count(initializer) != 1:
        raise ValueError("Missing RAM initial-state abstraction target")
    # Leave every functional driver intact. Other initial words become arbitrary;
    # the constant watched word retains its exact declared initial value. Proving
    # this superset is cheaper than solving 32,768 irrelevant initializer stores.
    actual = module.replace(initializer, "// Initial state is abstracted at the watched word.")
    if (
        len(re.findall(r"wire\s+\[7:0\]\s+data\s*;", actual)) != 1
        or len(re.findall(r"reg\s+io_cpuEnable_regNext\s*;", actual)) != 1
    ):
        raise ValueError("Changed shared-data or CPU-ready observation binding")
    header = actual.index(");")
    actual = (
        actual[:header]
        + ", output wire [7:0] proofSharedData, output wire proofCpuReady\n"
        + actual[header : actual.rindex("endmodule")]
        + "assign proofSharedData=data;\nassign proofCpuReady=io_cpuEnable_regNext;\nendmodule\n"
    )
    return (
        actual
        + """
module RamContract(input clk, reset, cpuEnable, cpuWrite, videoEnable,
 input [14:0] cpuAddress, videoAddress, input [7:0] cpuData);
 (* anyconst *) reg [14:0] watched;
 wire [7:0] memoryValue, videoData, cpuReadData, sharedData;
 wire actualCpuReady;
 BbcRam actual(.clk(clk),.reset(reset),.io_cpuEnable(cpuEnable),.io_cpuWrite(cpuWrite),
 .io_cpuAddress(cpuAddress),.io_cpuData(cpuData),.io_videoEnable(videoEnable),
 .io_videoAddress(videoAddress),.io_videoReadData(videoData),
 .io_cpuReadData(cpuReadData),.proofSharedData(sharedData),.proofCpuReady(actualCpuReady),
 .io_debugAddress(watched),.io_debugData(memoryValue));
 reg [7:0] expectedCell;
 reg [7:0] expectedVideo=0;
 reg validVideo=0;
 reg [7:0] cpuFirst=0, expectedCpu=0;
 reg cpuReady=0, cpuFirstValid=0, validCpu=0;
 initial begin
  assume(reset);
  assume(expectedCell==(watched<256 ? 8'h00 : 8'hff));
  assume(memoryValue==expectedCell);
 end
 always @* begin
  assume(!(cpuEnable && videoEnable));
  assert(memoryValue==expectedCell);
  if(validVideo) assert(videoData==expectedVideo);
  assert(actualCpuReady==cpuReady);
  if(cpuReady && cpuFirstValid) assert(sharedData==cpuFirst);
  if(validCpu) assert(cpuReadData==expectedCpu);
 end
 // RAM persists across reset, and its write port itself is not reset-gated.
 always @(posedge clk)
  if(cpuEnable && cpuWrite && cpuAddress==watched) expectedCell<=cpuData;
 always @(posedge clk or posedge reset)
 if(reset) begin expectedVideo<=0; validVideo<=0; end
 else if(videoEnable) begin
  expectedVideo<=expectedCell; validVideo<=videoAddress==watched;
 end
 // The CPU response has a further holding register after the shared memory
 // port. A request becomes visible there on the following master edge.
 always @(posedge clk) if(cpuEnable) cpuFirst<=expectedCell;
 always @(posedge clk or posedge reset)
 if(reset) begin cpuReady<=0; cpuFirstValid<=0; expectedCpu<=0; validCpu<=0; end
 else begin
  cpuReady<=cpuEnable;
  if(cpuEnable) cpuFirstValid<=!cpuWrite && cpuAddress==watched;
  if(cpuReady) begin expectedCpu<=cpuFirst; validCpu<=cpuFirstValid; end
 end
endmodule
"""
    )


def observed_parent(text, top):
    """Observe real RAM ports and strengthen the actual phase relation."""
    match = re.search(r"^module\s+" + top + r"\b[\s\S]*?^endmodule\b", text, re.M)
    if match is None:
        raise ValueError("Missing actual RAM parent")
    parent = match[0]

    def child(module, instance):
        match = re.search(r"\b" + module + r"\s+" + instance + r"\s*\(([\s\S]*?)\n\s*\);", parent)
        if match is None:
            raise ValueError("Missing RAM parent child: " + instance)
        ports = {
            name: re.sub(r"\s+", "", value)
            for name, value in re.findall(r"\.(\w+)\s*\(([^()]*)\)", match[1])
        }
        return match, ports

    _, ram = child("BbcRam", "ram")
    video_child, video = child("BbcVideo", "video")
    required = {
        "ram": (
            "clk",
            "reset",
            "io_cpuEnable",
            "io_videoEnable",
            "io_videoAddress",
            "io_videoReadData",
        ),
        "video": ("clk", "reset", "io_memoryRead", "io_memoryAddress", "io_memoryData"),
    }
    if not set(required["ram"]).issubset(ram) or not set(required["video"]).issubset(video):
        raise ValueError("Missing actual RAM/video port")
    if not (
        ram["clk"] == video["clk"] == "clk"
        and ram["reset"] == video["reset"] == "reset"
        and ram["io_videoEnable"] == video["io_memoryRead"]
        and ram["io_videoAddress"] == video["io_memoryAddress"]
        and ram["io_videoReadData"] == video["io_memoryData"]
    ):
        raise ValueError("Actual RAM/video connections differ from the proved contract")
    if re.search(r"reg\s+\[4:0\]\s+phase_1;", parent) is None:
        raise ValueError("Unrecognized actual parent phase register")
    replacement = video_child[0]
    closing = replacement.rindex(");")
    replacement = (
        replacement[:closing]
        + ", .proofMaster(proof_master), .proofDivider(proof_divider)\n"
        + replacement[closing:]
    )
    parent = parent[: video_child.start()] + replacement + parent[video_child.end() :]
    additions = (
        "wire [4:0] proof_master; wire [1:0] proof_divider;\n"
        "reg proof_started=0;\nalways @(posedge clk) if(reset) proof_started<=1;\n"
        "wire proof_valid = !proof_started || (\n!("
        + ram["io_cpuEnable"]
        + " && "
        + ram["io_videoEnable"]
        + ") && phase_1==(proof_master ^ 5'h10)"
    )
    if top == "BbcMachine96":
        additions += " && _zz_clock32==proof_divider"
    additions += ");\n"
    parent = parent[: parent.rindex("endmodule")] + additions + "endmodule"
    text = text[: match.start()] + parent + text[match.end() :]
    match = re.search(r"^module\s+BbcVideo\b[\s\S]*?^endmodule\b", text, re.M)
    if match is None or re.search(r"reg\s+\[4:0\]\s+master;", match[0]) is None:
        raise ValueError("Missing actual video master register")
    body = match[0]
    closing = body.index(");")
    body = (
        body[:closing]
        + ",\n output wire [4:0] proofMaster,\n output wire [1:0] proofDivider\n"
        + body[closing : body.rindex("endmodule")]
        + "assign proofMaster=master;\nassign proofDivider="
        + ("_zz_clock32" if top == "BbcMachine96" else "2'b00")
        + ";\nendmodule"
    )
    return text[: match.start()] + body + text[match.end() :], ram


def qualify():
    if not build.generation_current():
        raise ValueError("Generate the current whole machine before proving shared RAM")
    generation96 = video96.generate()
    directory = build.BBC / "ram-proof"
    directory.mkdir(exist_ok=True)
    z3 = shutil.which("z3") or str(build.BBC / "formal-tools/bin/z3")
    smtbmc = shutil.which("yosys-smtbmc")
    if not Path(z3).is_file() or smtbmc is None:
        raise ValueError(
            "RAM induction needs yosys-smtbmc and Z3; install z3-solver==4.13.4.0 into build/bbc/formal-tools"
        )
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
    sources = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    actual = device_rtl(["BbcRam"])
    image, name = initial_image(actual, sources[0].parent)
    other = re.search(r"^module\s+BbcRam\b[\s\S]*?^endmodule\b", sources[1].read_text(), re.M)[0]
    other_image, other_name = initial_image(other, sources[1].parent)
    actual_body = re.search(r"^module\s+BbcRam\b[\s\S]*?^endmodule\b", actual, re.M)[0]
    if actual_body != other.replace(other_name, name):
        raise ValueError("Actual RAM children differ between master-clock variants")
    dependencies = [
        Path(__file__),
        ROOT / "tools/bbc/probe.py",
        ROOT / "rtl/bbc/BbcCpu.v",
        *sources,
        image,
        other_image,
        Path(z3),
        Path(smtbmc),
        data / "smtio.py",
    ]

    def inputs():
        return {str(path): digest(path) for path in dependencies}

    before = inputs()

    def execute(command, label, cwd=ROOT):
        result = subprocess.run(
            command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=120
        )
        (directory / (label + ".log")).write_text(result.stdout + result.stderr)
        return result

    def compile_model(text, label):
        source = directory / (label + ".v")
        source.write_text(text)
        smt = directory / (label + ".smt2")
        execute(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_verilog -formal -D SYNTHESIS -sv {source}; prep -top RamContract -flatten; async2sync; chformal -lower; dffunmap; opt_clean; write_smt2 -wires {smt}",
            ],
            label + "-compile",
            image.parent,
        ).check_returncode()
        return smt

    model = contract(actual, name)
    smt = compile_model(model, "contract")

    def solve(smt, label, induction=False):
        result = execute(
            [
                smtbmc,
                "-s",
                "z3",
                *(["-i"] if induction else []),
                "-t",
                "6",
                "--dump-vcd",
                str(directory / (label + ".vcd")),
                str(smt),
            ],
            label,
        )
        return result.returncode == 0 and "Status: PASSED" in result.stdout, result

    bounded, _ = solve(smt, "bounded")
    inductive, _ = solve(smt, "induction", True)
    hold = "assign io_videoReadData = (videoReady ? data : videoResponse);"
    if model.count(hold) != 1:
        raise ValueError("Missing actual video-hold negative-control target")
    bad_smt = compile_model(model.replace(hold, "assign io_videoReadData = data;"), "wrong-hold")
    _, rejected = solve(bad_smt, "negative")
    detected = rejected.returncode != 0 and "Status: FAILED" in rejected.stdout
    cpu_hold = "if(io_cpuEnable_regNext) begin"
    if model.count(cpu_hold) != 1:
        raise ValueError("Missing actual CPU-hold negative-control target")
    bad_cpu_smt = compile_model(model.replace(cpu_hold, "if(1'b1) begin"), "wrong-cpu-hold")
    _, rejected_cpu = solve(bad_cpu_smt, "negative-cpu")
    cpu_detected = rejected_cpu.returncode != 0 and "Status: FAILED" in rejected_cpu.stdout
    parents = []
    for source, top in zip(sources, ("BbcMachine", "BbcMachine96"), strict=True):
        observed, ports = observed_parent(source.read_text(), top)
        wrapper = directory / (top + "-observed.v")
        wrapper.write_text(observed)
        children = "BbcCpu BbcCpuEvents BbcRam Via6522 Adc7002 BbcAudio Intel8271 BbcSystemPins Crtc6845 BbcVideoAddress"
        if top == "BbcMachine96":
            children += " Saa5050Cell"
        command = (
            f"read_verilog {wrapper} {ROOT / 'rtl/bbc/BbcCpu.v'}; blackbox {children}; hierarchy -top {top}; proc; "
            f"cutpoint {top}/w:cpu_address {top}/w:cpu_write_enable {top}/w:cpu_write_data; "
            f"delete -output {top}/o:*; expose {top}/w:proof_valid; opt_clean -purge; flatten; opt_clean; async2sync; "
            "sat -set-init-zero -tempinduct -seq 3 -maxsteps 8 -prove proof_valid 1 -verify -timeout 90"
        )
        positive = execute(["yosys", "-Q", "-T", "-p", command], top + "-parent", source.parent)
        # Change the real RAM input; the observation must follow that change.
        child = re.search(r"\bBbcRam\s+ram\s*\(([\s\S]*?)\n\s*\);", source.read_text())
        broken_child, count = re.subn(
            r"(\.io_cpuEnable\s*\()([^()]*)(\))", r"\g<1>1'b1\g<3>", child[0]
        )
        if count != 1:
            raise ValueError("Missing actual RAM collision negative-control target")
        broken = (
            source.read_text()[: child.start()] + broken_child + source.read_text()[child.end() :]
        )
        negative = directory / (top + "-wrong-port.v")
        negative.write_text(observed_parent(broken, top)[0])
        rejected = execute(
            ["yosys", "-Q", "-T", "-p", command.replace(str(wrapper), str(negative))],
            top + "-parent-negative",
            source.parent,
        )
        negative_detected = rejected.returncode != 0 and "proof did fail" in rejected.stderr
        parents.append(
            {
                "machine": top,
                "passed": positive.returncode == 0 and negative_detected,
                "negative_control_detected": negative_detected,
                "connections": ports,
            }
        )
    unchanged = (
        before == inputs() and build.generation_current() and generation96 == video96.snapshot()
    )
    report = {
        "passed": bounded
        and inductive
        and detected
        and cpu_detected
        and unchanged
        and all(p["passed"] for p in parents),
        "bounded_passed": bounded,
        "induction_passed": inductive,
        "negative_control_detected": detected and cpu_detected,
        "negative_controls": {"video_hold": detected, "cpu_hold": cpu_detected},
        "parent_bindings": parents,
        "inputs": before,
        "inputs_unchanged": unchanged,
        "solver": subprocess.check_output([z3, "--version"], text=True).strip(),
        "artifacts": build.hashes(
            p for p in directory.iterdir() if p.suffix in {".v", ".vcd", ".smt2", ".log"}
        ),
        "scope": "A fixed arbitrary address covers every one of 32,768 RAM words. Temporal induction checks independent accepted CPU writes, the shared read port, CPU response capture on the master edge following a request and response retention, and video fetch retention across CPU requests. Every other initial word is unconstrained; the watched initial word follows the fully checked declared image. The actual parent at both rates proves non-overlapping RAM requests and correct video address/data wiring after reset. CPU bus values are arbitrary cutpoints for the parent proof; functional phase logic is unchanged.",
        "assumptions": "Local memory proof starts in reset and assumes disjoint CPU/video requests. The latter is discharged by both actual-parent proofs. Initial array abstraction only relaxes unrelated initial words; it assumes no subsequent output equality. SYNTHESIS disables the redundant simulation assertion, while the independent contract assertions remain active.",
        "limits": "Digital shared-RAM/video contract, not a universal CPU, renderer or game proof. Does not specify CPU readback during writes, unclocked physical RAM behavior or arbitrary initial power-on bytes outside the declared profile.",
    }
    write_json(directory / "result.json", report)
    print(f"Actual shared RAM/video proof: {report['passed']}", flush=True)
    if not report["passed"]:
        raise ValueError("Shared RAM proof failed; logs and counterexamples are retained")
    return report


if __name__ == "__main__":
    qualify()
