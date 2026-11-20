"""Bind original bit-step candidate delivery and initialized counter bounds."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition
from tools.bbc.proofs.cpu_reference import (
    callback_cpu,
    callback_order,
    fdc_epoch,
    fdc_events,
    fdc_order,
    prepared,
)
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/fdc-source-sync"
WIDTH = 934
PROPERTIES = ("prefixEquivalent", "ageBound", "checkingBound", "countBound")
FIELDS = {
    "syncAge": 54,
    "syncShift": 16,
    "syncCount": 5,
    "syncChecking": 1,
    "phase_1": 4,
    "headerSeen": 1,
    "headerRecovering": 1,
    "mechanicalPhase": 2,
    "mechanicalDelay": 24,
    "dispatchPending": 1,
}
EXPORTS = {
    "seen": ("syncByteEvent", 1),
    "clock": ("syncCandidateClock", 8),
    "data": ("syncCandidateData", 8),
    "bit": ("syncCandidateBit", 4),
    "cell": ("cellBoundary", 1),
    "present": ("headerPresent", 1),
    "indexPulse": ("index", 1),
}


def indices(body):
    state = body["ports"]["proofState"]["bits"]
    if (
        len(state) != WIDTH
        or len(set(state)) != WIDTH
        or len(body["ports"]["proofNext"]["bits"]) != WIDTH
    ):
        raise ValueError("Incomplete sync controller state")
    fields, used = {}, set()
    for name, width in FIELDS.items():
        bits = body["netnames"][name]["bits"]
        if len(bits) != width or not set(bits).issubset(state) or used.intersection(bits):
            raise ValueError("Changed sync state: " + name)
        fields[name] = [state.index(bit) for bit in bits]
        used.update(bits)
    return fields


def reset_frame(raw):
    body = raw["modules"]["Intel8271"]
    for name in ("syncAge", "syncCount", "syncChecking"):
        bits = set(body["netnames"][name]["bits"])
        covered = set()
        for cell in body["cells"].values():
            if not bits.intersection(cell["connections"].get("Q", [])):
                continue
            if (
                cell["type"] != "$adff"
                or cell["connections"]["ARST"] != body["ports"]["reset"]["bits"]
                or int(cell["parameters"]["ARST_POLARITY"], 2) != 1
                or int(cell["parameters"]["ARST_VALUE"], 2) != 0
            ):
                raise ValueError("Sync counter frame does not start from actual zero reset")
            covered.update(bits.intersection(cell["connections"]["Q"]))
        if covered != bits:
            raise ValueError("Missing sync reset storage")


def fault(source):
    matches = re.findall(r"(?m)^  assign syncCandidateData = [^;]+;$", source)
    if len(matches) != 1:
        raise ValueError("Changed actual delimited-byte binding")
    return replace_once(source, matches[0], matches[0][:-1] + " ^ 8'h01;")


def contract(body, enable):
    fields = indices(body)

    def q(name, vector="proofState"):
        return "{" + ",".join(f"{vector}[{i}]" for i in reversed(fields[name])) + "}"

    inputs, ports = [], []
    for name, port in body["ports"].items():
        if port["direction"] == "input":
            inputs.append(f"input[{len(port['bits']) - 1}:0] {name}")
            ports.append(f".{name}({name})")
    ports.extend(f".sync_{name}({name})" for name in EXPORTS)
    return f"""module SyncBinding({",".join(inputs)},input[15:0]sourceHigh,output {",".join(PROPERTIES)});
wire[{WIDTH - 1}:0]following;
wire seen,cell,present,indexPulse;wire[7:0]clock,data;wire[3:0]bit;
FdcStateNext actual({",".join(ports)},.proofNext(following));
wire candidate,trap;wire[7:0]sourceClock,sourceData;wire[3:0]sourceBit;
FdcSyncSource source(.age({q("syncAge")}),.shift({{sourceHigh,{q("syncShift")}}}),.count({q("syncCount")}),.checking({q("syncChecking")}),.word(io_storageData),.length(5'd16),.candidate(candidate),.trap(trap),.clockByte(sourceClock),.dataByte(sourceData),.candidateBit(sourceBit));
wire base={enable} && !reset && !io_warmReset && !io_read && !io_write && !indexPulse && !{q("dispatchPending")} &&
 ({q("mechanicalPhase")}==0 || {q("mechanicalDelay")}>0) && {q("phase_1")}==7 && !{q("headerSeen")} && !{q("headerRecovering")} && present && cell &&
 {q("syncAge")}<=54'h20000000000000 && {q("syncCount")}<16 && (!{q("syncChecking")} || {q("syncAge")}==0);
assign prefixEquivalent=!base || (!trap && seen==candidate && (!candidate || (clock==sourceClock && data==sourceData && bit==sourceBit)));
assign ageBound={q("syncAge")}>54'h20000000000000 || {q("syncAge", "following")}<=54'h20000000000000;
assign checkingBound=({q("syncChecking")} && {q("syncAge")}!=0) || !{q("syncChecking", "following")} || {q("syncAge", "following")}==0;
assign countBound={q("syncCount")}>=16 || {q("syncCount", "following")}<16;
endmodule
"""


def formal(multiplier, parent, top):
    controller, extra = callback_order.fdc_instance(parent.read_text(), top)
    checks, artifacts = [], []
    for name, text in (("positive", controller), ("wrong-delimited-byte", fault(controller))):
        case = DIRECTORY / f"formal-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        header = ",".join(f"output[{w - 1}:0]sync_{n}" for n, (_, w) in EXPORTS.items())
        text = replace_once(text, "module Intel8271 (", "module Intel8271 (" + header + ",")
        text = replace_once(
            text,
            "endmodule",
            "\n"
            + "".join(f"assign sync_{n}={signal};\n" for n, (signal, _) in EXPORTS.items())
            + "endmodule",
        )
        model, raw, cut, wrapper, graph = [
            case / n for n in ("actual.v", "raw.json", "cut.json", "contract.v", "graph.json")
        ]
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(parent.parent / m[1]) + '"',
                text,
            )
        )
        prepared.prepare(
            case,
            f"read_verilog -D SYNTHESIS {model}; hierarchy -top Intel8271; proc; memory_map; opt; dffunmap; check -assert; write_json {raw}",
            [model],
            [raw],
        )
        raw_data = json.loads(raw.read_text())
        reset_frame(raw_data)
        data = callback_cpu.cut_fdc(raw_data)
        body = data["modules"]["FdcStateNext"]
        write_json(cut, data)
        wrapper.write_text(contract(body, extra[0] if extra else "1'b1"))
        lower = case / "lower"
        lower.mkdir(exist_ok=True)
        prepared.prepare(
            lower,
            f"read_json {cut}; read_verilog {DIRECTORY / 'source.v'} {wrapper}; prep -top SyncBinding -flatten; opt; check -assert; write_json {graph}",
            [cut, DIRECTORY / "source.v", wrapper],
            [graph],
        )
        callback_cpu.guard_inputs(
            json.loads(graph.read_text()),
            "SyncBinding",
            {
                **{
                    n: len(p["bits"]) for n, p in body["ports"].items() if p["direction"] == "input"
                },
                "sourceHigh": 16,
            },
        )
        for prop in PROPERTIES if name == "positive" else PROPERTIES[:1]:
            query = case / prop
            query.mkdir(exist_ok=True)
            slim = query / "graph.json"
            others = " ".join("SyncBinding/w:" + n for n in PROPERTIES if n != prop)
            prepared.prepare(
                query,
                f"read_json {graph}; delete -output {others}; opt_clean -purge; check -assert; write_json {slim}",
                [graph],
                [slim],
            )
            callback_cpu.guard_inputs(
                json.loads(slim.read_text()),
                "SyncBinding",
                {
                    **{
                        n: len(p["bits"])
                        for n, p in body["ports"].items()
                        if p["direction"] == "input"
                    },
                    "sourceHigh": 16,
                },
            )
            artifacts.extend(
                callback_cpu.prove(
                    query,
                    f"read_json {slim}; opt_clean -purge",
                    prop,
                    name == "positive",
                    horizon=0,
                    show="-show-inputs",
                )
            )
            artifacts.extend([slim, query / "prepared.json", query / "prepare.log"])
            checks.append(
                {"name": name, "property": prop, "clock_multiplier": multiplier, "accepted": True}
            )
        artifacts.extend(
            [
                model,
                raw,
                cut,
                wrapper,
                graph,
                case / "prepared.json",
                case / "prepare.log",
                lower / "prepared.json",
                lower / "prepare.log",
            ]
        )
    return checks, artifacts


def kernel_checks():
    source = (DIRECTORY / "source.v").read_text()
    checks, artifacts = [], []
    for name in ("positive", "wrong-byte", "early-plateau"):
        case = DIRECTORY / ("kernel-" + name)
        case.mkdir(exist_ok=True)
        text = source
        if name == "wrong-byte":
            matches = re.findall(r"(?m)^dataByte = local[0-9]+;$", text)
            if len(matches) != 16:
                raise ValueError("Changed source callback argument inventory")
            text = re.sub(r"(?m)^dataByte = (local[0-9]+);$", r"dataByte = \1 ^ 64'd1;", text)
        if name == "early-plateau":
            if text.count("64'h20000000000000") != 16:
                raise ValueError("Changed source integer-increment boundary")
            text = text.replace("64'h20000000000000", "64'h1fffffffffffff")
        model = case / "actual.v"
        model.write_text(text)
        binary = callback_order.compile_native(
            model, case, "FdcSyncSource", ROOT / "sim/native/bbc_fdc_sync_source_test.cpp"
        )
        trial = subprocess.run(
            [str(binary), str(DIRECTORY), "53", "5936"], capture_output=True, text=True, timeout=120
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            trial.returncode == 0
            and not trial.stderr
            and trial.stdout
            == "Original sync prefix: 3473408 word cases; 5936 partial cases; 0 differences\n"
            if name == "positive"
            else trial.returncode == 1
            and not trial.stderr
            and re.fullmatch(r"SYNC_PREFIX_MISMATCH word frame [0-9]+ word [0-9]+\n", trial.stdout)
            is not None
        )
        if not accepted:
            raise ValueError("Source sync oracle differs: " + str(log))
        checks.append({"name": "kernel-" + name, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
        if name == "positive":
            broken = case / "last-record"
            broken.mkdir(exist_ok=True)
            for n in ("frames.tsv", "short.tsv", "original.bin"):
                shutil.copyfile(DIRECTORY / n, broken / n)
            with (broken / "original.bin").open("r+b") as output:
                output.seek(-1, 2)
                byte = output.read(1)[0]
                output.seek(-1, 2)
                output.write(bytes([byte ^ 1]))
            control = subprocess.run(
                [str(binary), str(broken), "53", "5936"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            control_log = case / "last-record.log"
            control_log.write_text(control.stdout + control.stderr)
            if (
                control.returncode != 1
                or control.stderr
                or control.stdout != "SYNC_PREFIX_MISMATCH word frame 52 word 65535\n"
            ):
                raise ValueError("Last source oracle record was not checked")
            checks.append({"name": "last-oracle-record", "accepted": True})
            artifacts.extend(
                [control_log, *[broken / n for n in ("frames.tsv", "short.tsv", "original.bin")]]
            )
    return checks, artifacts


def native(multiplier, parent, top):
    public = DIRECTORY / "public"
    selected = fdc_events.fixtures((public / "original.tsv").read_text(), fdc_order.NAMES)
    table = DIRECTORY / "public.tsv"
    table.write_text(re.sub(r"\Acase [0-9]+ ", "case 0 ", selected["rephased-byte-ack"]))
    observations = int(table.read_text().splitlines()[0].split()[-1])
    checks, artifacts = [], [table]
    for name, text in (
        ("positive", parent.read_text()),
        ("wrong-delimited-byte", fault(parent.read_text())),
    ):
        case = DIRECTORY / f"native-{name}{multiplier}"
        case.mkdir(exist_ok=True)
        model = case / "actual.v"
        model.write_text(
            re.sub(
                r'\$readmemb\("([^\"]+)"',
                lambda m: '$readmemb("' + str(parent.parent / m[1]) + '"',
                fdc_epoch.native_model(text, top),
            )
        )
        binary = callback_order.compile_native(
            model,
            case,
            "DeviceEpochProbe",
            ROOT / "sim/native/bbc_fdc_branch_test.cpp",
            [ROOT / "sim/native/bbc_disc_payload.h", ROOT / "sim/native/bbc_fm_media.h"],
        )
        trial = subprocess.run(
            [str(binary), str(table), str(ROOT / "build/bbc/disc.ssd"), str(multiplier), "1"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = case / "run.log"
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            trial.returncode == 0
            and not trial.stderr
            and trial.stdout
            == f"FDC epoch: 1 cold cases; {observations} observations; 0 differences\n"
            if name == "positive"
            else fdc_events.negative_result(trial, "rephased-byte-ack")
        )
        if not accepted:
            raise ValueError("Source sync public control differs: " + str(log))
        checks.append({"name": "native-" + name, "clock_multiplier": multiplier, "accepted": True})
        artifacts.extend([model, binary, log, case / "build.json", case / "build.log"])
    return checks, artifacts


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    reference = source_root("jsbeeb")
    helper = ROOT / "tools/bbc/fdc_sync_source.mjs"
    clocks = [current_machine.DIRECTORY / f"clock{m}/result.json" for m in (1, 3)]
    for p in clocks:
        memory_composition.sealed(p)
    files = [
        Path(__file__),
        helper,
        ROOT / "sim/native/bbc_fdc_sync_source_test.cpp",
        ROOT / "sim/native/bbc_fdc_branch_test.cpp",
        ROOT / "sim/native/bbc_disc_payload.h",
        ROOT / "sim/native/bbc_fm_media.h",
        ROOT / "tools/bbc/reference_drive.mjs",
        ROOT / "node_modules/prettier/plugins/babel.mjs",
        ROOT / "build/bbc/disc.ssd",
        *[ROOT / n for n in current_machine.generation_inputs()],
        current_machine.DIRECTORY / "generation.json",
        *clocks,
        *[p for _, p, _ in current_machine.parents()],
        *[b for _, p, _ in current_machine.parents() for b in p.parent.glob("*.bin")],
        *sorted((reference / "src").rglob("*.js")),
        *[
            Path(m.__file__)
            for m in (
                current_machine,
                memory_composition,
                callback_cpu,
                callback_order,
                fdc_epoch,
                fdc_events,
                fdc_order,
                prepared,
            )
        ],
        Path(fdc_order.__file__).with_suffix(".mjs"),
        ROOT / "tools/bbc/proofs/video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(t)) for t in ("node", "yosys", "verilator", "c++")],
    ]
    before = hashes(files)
    subprocess.run(["node", str(helper), str(reference), str(DIRECTORY)], check=True, timeout=180)
    projection = json.loads((DIRECTORY / "projection.json").read_text())
    if projection.get("passed") is not True or [
        projection.get(k) for k in ("frames", "word_cases", "short_cases")
    ] != [53, 3473408, 5936]:
        raise ValueError("Incomplete original sync projection")
    checks, artifacts = kernel_checks()
    public = DIRECTORY / "public"
    public.mkdir(exist_ok=True)
    subprocess.run(
        [
            "node",
            str(Path(fdc_order.__file__).with_suffix(".mjs")),
            str(reference),
            str(ROOT / "tools/bbc/reference_drive.mjs"),
            str(ROOT / "build/bbc/disc.ssd"),
            str(public),
        ],
        check=True,
        timeout=120,
    )
    fdc_order.original(public / "original.json")
    for m, parent, top in current_machine.parents():
        for producer in (formal, native):
            c, a = producer(m, parent, top)
            checks.extend(c)
            artifacts.extend(a)
            print(f"Source sync {m} {producer.__name__}: PASS", flush=True)
    artifacts.extend(
        [
            DIRECTORY / n
            for n in ("source.v", "projection.json", "frames.tsv", "original.bin", "short.tsv")
        ]
    )
    artifacts.extend([public / "original.json", public / "original.tsv"])
    current_machine.audit()
    if before != hashes(files):
        raise ValueError("Source sync inputs changed")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "register_bits": WIDTH,
            "scope": "Original AST-lowered sync/check-ID prefix ending at the first byte-callback boundary, with exact dense-integer Number increment through the 2^53 plateau and Int32 operations. Original execution checks every 16-bit word across 53 counter/partial-shift frames and 5936 shorter prefixes. All-state actual 934-bit controller queries at 32/96 MHz compare candidate presence, byte values and bit position under the declared received-word, sync/check-ID and no-host/index/earlier-callback domain. Three inductive counter-frame implications have actual zero-reset witnesses. A corrupted actual delimited byte must fail formally and in a cold public read; two kernel faults and the final oracle record must fail natively.",
            "limits": "Logging is disabled. The received clock/data word must correspond to the original bit input. Byte-callback effects, complete scanner continuation-state correspondence, header/payload framing, raw weak-cell/drive coupling and full initialized source/controller refinement remain open. The larger continuation queries timed out and supply no premise. Public drive execution remains finite; whole-machine/game equivalence is not established.",
            "full_equivalence": False,
            "universal_game_equivalence": False,
        },
    )


if __name__ == "__main__":
    qualify()
