"""Prove actual ADC, VIA, video, keyboard and sound routing at both clocks."""

import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc import build, bus_binding_proof, video96
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json


def observe(source, top):
    parent = bus_binding_proof.module(source, top)
    ports = {
        name: bus_binding_proof.ports(parent, kind, name)
        for kind, name in (
            ("Via6522", "sys"),
            ("Via6522", "user"),
            ("BbcVideo", "video"),
            ("Adc7002", "adc"),
            ("BbcSystemPins", "systemPins"),
            ("BbcAudio", "audio"),
        )
    }
    sys, user, video, adc, pins, audio = [
        ports[name] for name in ("sys", "user", "video", "adc", "systemPins", "audio")
    ]
    via_register = sys["io_address"]
    requirements = [
        (video["io_lightPenPulse"], sys["io_cb2Rise"]),
        (sys["io_ca1"], video["io_busVsync"]),
        (sys["io_ca2"], pins["io_busCa2"]),
        (
            sys["io_ca2AfterWrite"],
            f"{sys['io_write']} && ({pins['io_ca2']} != {pins['io_busCa2']})",
        ),
        (sys["io_cb1"], f"{sys['io_cb1Output']} ? {sys['io_cb1Pin']} : {adc['io_eocBusN']}"),
        (sys["io_cb1Pulse"], f"{adc['io_eocPulse']} && !{sys['io_cb1Output']}"),
        (sys["io_cb2"], f"{sys['io_cb2Output']} && {sys['io_cb2Pin']}"),
        (user["io_cb1"], f"{user['io_cb1Output']} && {user['io_cb1Pin']}"),
        (user["io_cb2"], f"{user['io_cb2Output']} && {user['io_cb2Pin']}"),
        (user["io_cb1Pulse"], "1'b0"),
        (user["io_ca1"], "1'b0"),
        (user["io_ca2"], "1'b0"),
        (user["io_ca2AfterWrite"], "1'b0"),
        (sys["io_portAIn"], pins["io_portAIn"]),
        (sys["io_portBIn"], "8'hff ^ {2'b0,io_joystickButtons,4'b0}"),
        (user["io_portAIn"], "8'hff"),
        (user["io_portBIn"], "8'hff"),
        (pins["io_keys"], "io_keys"),
        (pins["io_driveA"], sys["io_driveA"]),
        (pins["io_busDriveA"], sys["io_busDriveA"]),
        (pins["io_busPortB"], sys["io_busPortB"]),
        (video["io_scroll"], pins["io_scroll"]),
        (audio["io_enabled"], pins["io_soundEnabled"]),
        (audio["io_data"], pins["io_soundData"]),
        (
            audio["io_touch"],
            f"{sys['io_write']} && (({via_register} & 4'hc) == 0 || {via_register} == 4'hf)",
        ),
        ("io_psgWrite", audio["io_write"]),
        ("io_psgData", audio["io_acceptedData"]),
        ("io_audioSample", audio["io_sample"]),
        (adc["io_channels"], "io_analogue"),
        (adc["io_tick"], "tickVia"),
        (sys["io_tick"], "tickVia"),
        (user["io_tick"], "tickVia"),
    ]
    removed, cuts = [], {"cpuEnable", "tickVia"}
    for kind, name in re.findall(r"^\s+(\w+)\s+(\w+)\s*\(\s*$", parent, re.M):
        if kind == "BbcAddressDecode":
            continue
        removed.append(name)
        outputs = (
            re.findall(
                r"output\s+(?:wire|reg)\s*(?:\[[^\]]+\]\s*)?(\w+)",
                bus_binding_proof.module(source, kind).split(");", 1)[0],
            )
            if kind not in {"BbcCpu", "BbcCpuEvents"}
            else [
                "address",
                "write_enable",
                "write_data",
                "instruction",
                "program_counter",
                "accumulator",
                "index_x",
                "index_y",
                "stack_pointer",
                "flags",
            ]
        )
        connections = bus_binding_proof.ports(parent, kind, name)
        for output in outputs:
            if output not in connections:
                continue
            net = re.sub(r"\[[^\]]*\]", "", connections[output]).strip()
            if not re.fullmatch(r"\w+", net):
                raise ValueError("Changed abstracted child output expression")
            cuts.add(net)
    addition = (
        "\nwire proofPins="
        + " &&\n".join(f"(({actual})==({expected}))" for actual, expected in requirements)
        + ";\n"
    )
    return (
        replace_once(source, parent, parent.removesuffix("endmodule") + addition + "endmodule"),
        removed,
        sorted(cuts),
    )


def qualify():
    if not build.generation_current():
        raise ValueError("Current parent elaboration required")
    directory = build.BBC / "peripheral-pin-binding-proof"
    directory.mkdir(exist_ok=True)
    sources = [
        (1, build.BBC / "BbcMachine.v", "BbcMachine"),
        (3, video96.DIRECTORY / "BbcMachine96.v", "BbcMachine96"),
    ]
    files = [
        Path(__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        ROOT / "rtl/bbc/BbcMachine.scala",
        ROOT / "rtl/bbc/BbcCpu.v",
        *[p for _, p, _ in sources],
        *build.BBC.glob("*.bin"),
        *video96.DIRECTORY.glob("*.bin"),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    clocks = []
    for multiplier, path, top in sources:
        source = path.read_text()
        parent = bus_binding_proof.module(source, top)
        video = bus_binding_proof.ports(parent, "BbcVideo", "video")
        light_pen = re.findall(
            r"\.io_lightPenPulse\s*\(\s*" + re.escape(video["io_lightPenPulse"]) + r"\s*\)",
            source,
        )
        if len(light_pen) != 1:
            raise ValueError("Changed actual light-pen input binding")
        variants = [
            ("positive", source),
            ("lost-light-pen", replace_once(source, light_pen[0], ".io_lightPenPulse(1'b0)")),
            (
                "lost-eoc-pulse",
                replace_once(
                    source,
                    "assign sys_io_cb1Pulse = (adc_io_eocPulse && (! sys_io_cb1Output));",
                    "assign sys_io_cb1Pulse = 1'b0;",
                ),
            ),
            (
                "wrong-cb1-bank",
                replace_once(
                    source,
                    "assign sys_io_cb1 = (sys_io_cb1Output ? sys_io_cb1Pin : adc_io_eocBusN);",
                    "assign sys_io_cb1 = adc_io_eocBusN;",
                ),
            ),
            (
                "lost-post-write-ca2",
                replace_once(
                    source,
                    "assign sys_io_ca2AfterWrite = (sys_io_write && (systemPins_io_ca2 != systemPins_io_busCa2));",
                    "assign sys_io_ca2AfterWrite = 1'b0;",
                ),
            ),
            (
                "lost-sound-ora-alias",
                replace_once(source, " || (portAddress == 4'b1111)", ""),
            ),
        ]
        rows = []
        for name, text in variants:
            observed, removed, cuts = observe(text, top)
            rtl = directory / f"{name}{multiplier}.v"
            rtl.write_text(observed)
            witness = directory / f"{name}{multiplier}-counterexample.json"
            witness.unlink(missing_ok=True)
            command = (
                f"read_verilog -formal -D SYNTHESIS {rtl} {ROOT / 'rtl/bbc/BbcCpu.v'}; delete "
                + " ".join(f"{top}/c:{child}" for child in removed)
                + f"; hierarchy -top {top}; proc; delete -output {top}/o:io_*; cutpoint "
                + " ".join(f"{top}/w:{cut}" for cut in cuts)
                + f"; expose {top}/w:proofPins; opt -purge; flatten; memory_map; opt; async2sync; opt_clean; check -assert; write_json {directory}/{name}{multiplier}.json; sat -set-init-zero -seq 2 -prove proofPins 1 -verify -show proofPins -dump_json {witness}"
            )
            log = directory / f"{name}{multiplier}.log"
            with log.open("w") as output:
                process = subprocess.run(
                    ["yosys", "-Q", "-T", "-p", command],
                    cwd=path.parent,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
            accepted = (
                process.returncode == 0
                and "SAT proof finished - no model found: SUCCESS!" in log.read_text()
                if name == "positive"
                else process.returncode != 0
                and "proof did fail!" in log.read_text()
                and witness.is_file()
            )
            rows.append({"name": name, "accepted": accepted})
            if not accepted:
                raise ValueError(f"Actual peripheral pin binding failed: {name}/{multiplier}")
        clocks.append({"clock_multiplier": multiplier, "checks": rows})
    stable = before == hashes(files)
    write_json(
        directory / "result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "artifacts": build.hashes(
                [p for p in directory.iterdir() if p.is_file() and p.name != "result.json"]
            ),
            "clocks": clocks,
            "scope": "Actual parent combinational routing at both rates: ADC EOC level/pulse bank selection, VIA return pins, video VSYNC/light-pen pulse, keyboard accepted-port/late-CA2 forwarding, joystick and analogue channels, scroll and shared VIA tick bindings. Includes sound WE/data, touches on accepted VIA ports and captured digital outputs. Child outputs and scheduler acceptance/tick outputs are independent cut inputs; actual parent expressions and decoder remain. Five actual parent wiring faults are detected at both clocks.",
            "limits": "Routing only, not child state semantics, event phase equivalence or whole-machine/game equivalence. Temporal scheduler contracts remain separate.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Actual peripheral pin binding inputs changed")


if __name__ == "__main__":
    qualify()
