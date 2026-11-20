"""Check native cold reset and the OS's first LDA against the history entry."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import foundation, frontend, history_recipe, mixed_rtl, prepared
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-lda-handoff"

HARNESS = """#define CHIPS_IMPL
#include "m6502.h"
#include <assert.h>
uint8_t nondet_byte(void);
int main(void) {
    m6502_t c;
    m6502_desc_t desc={0};
    uint64_t pins=m6502_init(&c,&desc);
    uint8_t low=nondet_byte(),high=nondet_byte();
    uint16_t vector=((uint16_t)high<<8)|low;
    for(unsigned step=0;step<9;step++) {
        uint16_t expected=step<2 ? 0 : step==2 ? 0x100 :
          step==3 ? 0x1ff : step==4 ? 0x1fe : step==5 ? 0xfffc :
          step==6 ? 0xfffd : step==7 ? vector : (uint16_t)(vector+1);
        assert((pins&0xffff)==expected);
        assert((pins&M6502_RW)!=0);
        assert(!!(pins&M6502_SYNC)==(step==0 || step==7));
        uint8_t byte=step==5 ? low : step==6 ? high :
          step==7 ? 0xa9 : step==8 ? 0x40 : nondet_byte();
        pins=(pins&~0xff0000ULL)|((uint64_t)byte<<16);
        pins=m6502_tick(&c,pins);
    }
    uint64_t expectedPins=0x03400000ULL|(uint16_t)(vector+2);
    assert(pins==expectedPins && c.PINS==expectedPins);
    assert(c.IR==0x54a && c.PC==(uint16_t)(vector+2) && c.AD==low);
    assert(c.A==0x40 && c.X==0 && c.Y==0 && c.S==0xfd && c.P==0x14);
    assert(c.irq_pip==0 && c.nmi_pip==0 && c.branch_irq==0 && c.brk_flags==0);
    assert(c.bcd_enabled && c.io_ddr==0 && c.io_out==0 && c.io_inp==0 && c.io_pins==0);
    return 0;
}
"""


def native_harness():
    text = replace_once(
        HARNESS,
        "#include <assert.h>",
        """#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#undef assert
#define assert(x) do {if(!(x)){fprintf(stderr,"NATIVE_ASSERT %s\\n",#x);exit(9);}}while(0)""",
    )
    text = replace_once(
        text,
        "uint8_t nondet_byte(void);",
        """static unsigned pattern,noise=0x5142;
static uint8_t nondet_byte(void) {
 noise=noise*1664525u+1013904223u;
 return pattern==0 ? 0 : pattern==1 ? 255 : pattern==2 ? 170 : noise>>24;
}""",
    )
    text = replace_once(text, "int main(void) {", "static void check(uint16_t vector) {")
    text = replace_once(
        text,
        "uint8_t low=nondet_byte(),high=nondet_byte();\n    uint16_t vector=((uint16_t)high<<8)|low;",
        "uint8_t low=vector&255,high=vector>>8;",
    )
    text = replace_once(text, "return 0;", "return;")
    return (
        text
        + """int main(void) {
 for(pattern=0;pattern<4;pattern++)
  for(unsigned vector=0;vector<65536;vector++)check(vector);
 puts("Native LDA: 262144 vectors; PASS");return 0;
}
"""
    )


def gate_contract(report, preservation):
    functions = re.findall(r"function supported;[\s\S]*?endfunction", preservation)
    contexts = re.findall(r"wire context=([^;]+);", preservation)
    if len(functions) != 1 or len(contexts) != 1:
        raise ValueError("Changed complete-to-selected admission contract")
    packed = 0
    for name, width in frontend.FIELDS:
        packed = (packed << width) | report["default_native_state"][name]
    prefix, _ = history_recipe.generate(
        report["default_native_state"], report["default_native_pins"]
    )
    entries = re.findall(r"referenceState<=\{([^;]+)\};referencePins<=", prefix)
    if len(entries) != 1:
        raise ValueError("Changed generalized source entry constructor")
    body = [
        "module NativeLdaEntry(output equivalent);",
        functions[0],
        "function admitted;input [255:0] state;input [63:0] pins;begin admitted="
        + contexts[0]
        + ";end endfunction",
        "(* anyseq *) reg [7:0] low,high,firstByte;",
        "(* anyseq *) reg [39:0] discarded;",
        "wire [15:0] vector={high,low},nextPc=vector+16'd2,base=vector-16'd11;",
        "wire [63:0] expectedPins=64'h03400000|{48'd0,nextPc};",
        f"wire [255:0] s0=256'h{packed:064x};",
        f"wire [63:0] p0=64'h{report['default_native_pins']:016x};",
    ]
    checks = []
    addresses = [
        "16'd0",
        "16'd0",
        "16'h100",
        "16'h1ff",
        "16'h1fe",
        "16'hfffc",
        "16'hfffd",
        "vector",
        "vector+16'd1",
    ]
    for i in range(9):
        byte = f"discarded[{i * 8}+:8]" if i < 5 else ["low", "high", "8'ha9", "8'h40"][i - 5]
        body.extend(
            [
                f"wire [255:0] s{i + 1};wire [63:0] p{i + 1};wire t{i};",
                f"wire [63:0] response{i}=(p{i}&~64'hff0000)|({{56'd0,{byte}}}<<16);",
                f"M6502ReferenceNext c{i}(.state_in(s{i}),.pins_in(response{i}),.state_out(s{i + 1}),.pins_out(p{i + 1}),.trap(t{i}));",
            ]
        )
        checks.extend(
            [
                f"admitted(s{i},response{i})",
                f"p{i}[15:0]==({addresses[i]})",
                f"p{i}[24]",
                f"p{i}[25]==1'b{int(i in (0, 7))}",
                f"!t{i}",
            ]
        )
    expected = dict(report["default_native_state"])
    expected.update(
        IR=0x54A, PC="nextPc", AD="{8'd0,low}", A=0x40, S=0xFD, P=0x14, PINS="expectedPins"
    )
    fields = [
        f"{width}'d{expected[name]}" if isinstance(expected[name], int) else expected[name]
        for name, width in frontend.FIELDS
    ]
    body.extend(
        [
            "wire [15:0] hiddenIR=16'h54a,hiddenAD={8'd0,low};",
            "wire [63:0] hiddenPins=expectedPins;wire [7:0] hiddenBranch=0;",
            "wire [7:0] a=8'h40,x=0,y=0,s=8'hfd,p=4;",
            "wire seedNmi=0,seedPrevious=0;wire [4:0] seedOlderIrq=0;",
            "wire [255:0] entry={" + entries[0] + "};",
            "wire [63:0] entryPins=64'h3000000|{48'd0,base+16'd13};",
            "wire [63:0] originalResponse=(p9&~64'hff0000)|({56'd0,firstByte}<<16);",
            "wire [63:0] entryResponse=(entryPins&~64'hff0000)|({56'd0,firstByte}<<16);",
        ]
    )
    checks.extend(
        [
            "s9=={" + ",".join(fields) + "}",
            "p9==expectedPins",
            "s9==entry",
            "originalResponse==entryResponse",
        ]
    )
    body.append(
        "assign equivalent=" + " && ".join("(" + check + ")" for check in checks) + ";\nendmodule\n"
    )
    return "\n".join(body)


def qualify():
    started = time.monotonic()
    parents = [foundation.DIRECTORY / "result.json", mixed_rtl.DIRECTORY / "result.json"]
    reports = [memory_composition.sealed(path) for path in parents]
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    model = mixed_rtl.DIRECTORY / "source.v"
    preservation = mixed_rtl.DIRECTORY / "preservation.v"
    files = [
        Path(__file__),
        *parents,
        header,
        model,
        preservation,
        *history_recipe.producer_files(),
        Path(frontend.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        *[Path(shutil.which(tool)) for tool in ("gcc", "yosys")],
    ]
    before = hashes(files)
    case = DIRECTORY / "gate-profile"
    case.mkdir(exist_ok=True)
    prop, graph, log = case / "contract.v", case / "graph.json", case / "proof.log"
    prop.write_text(gate_contract(reports[0], preservation.read_text()))
    prepared.prepare(
        case,
        f"read_verilog -formal -sv {model} {prop}; prep -top NativeLdaEntry -flatten; check -assert; opt -full; write_json {graph}",
        [model, prop],
        [graph],
    )
    if any(
        cell["type"] in ("$assume", "$check")
        for cell in json.loads(graph.read_text())["modules"]["NativeLdaEntry"]["cells"].values()
    ):
        raise ValueError("LDA admission/profile acquired an assumption")
    with log.open("w") as output:
        trial = subprocess.run(
            [
                "yosys",
                "-Q",
                "-T",
                "-p",
                f"read_json {graph}; sat -prove equivalent 1 -verify -timeout 120",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    gate_passed = trial.returncode == 0 and "SUCCESS!" in log.read_text()
    print("Complete-source LDA profile: " + ("PASS" if gate_passed else "FAIL"), flush=True)
    if not gate_passed:
        raise ValueError("LDA profile/admission/entry proof failed")
    text = header.read_text()
    wrong_lda = "case (0xA9<<3)|1: c->A=_GD();_NZ(c->A);_FETCH();break;"
    if text.count(wrong_lda) != 1 or text.count("c->AD=0xFFFC;") != 1:
        raise ValueError("Changed actual C mutation site")
    checks = []
    for name, body, correct in (
        ("positive", text, True),
        (
            "lost-lda-result",
            text.replace(wrong_lda, wrong_lda.replace("c->A=_GD();", "c->A=0;")),
            False,
        ),
        ("wrong-reset-vector", text.replace("c->AD=0xFFFC;", "c->AD=0xFFFE;"), False),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        (case / "m6502.h").write_text(body)
        program, binary, log = case / "native.c", case / "native", case / "native.log"
        program.write_text(native_harness())
        subprocess.run(
            ["gcc", "-O2", "-std=c11", str(program), "-o", str(binary)], check=True, timeout=60
        )
        trial = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        log.write_text(trial.stdout + trial.stderr)
        accepted = (
            trial.returncode == 0 and trial.stdout == "Native LDA: 262144 vectors; PASS\n"
            if correct
            else trial.returncode == 9 and "NATIVE_ASSERT " in trial.stderr
        )
        checks.append({"name": name, "accepted": accepted})
        print("Native LDA handoff " + name + (": PASS" if accepted else ": FAIL"), flush=True)
        if not accepted:
            break
    for path in parents:
        memory_composition.sealed(path)
    stable = before == hashes(files)
    passed = stable and len(checks) == 3 and all(check["accepted"] for check in checks)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(
                [
                    p
                    for name in ("gate-profile", *[check["name"] for check in checks])
                    for p in (DIRECTORY / name).glob("*")
                    if p.is_file()
                ]
            ),
            "wall_seconds": time.monotonic() - started,
            "checks": checks,
            "scope": "Nine source transitions after native construction: seven reset slots and LDA #$40, arbitrary vector/discarded bytes, quiet IRQ/NMI. Fresh SAT checks every bus slot, trap, all256 state bits, live pins and equality to the exact generalized history initializer, including first-response equality for every opcode byte. Every selected-decoder admission is proved, not assumed; sealed all321-output preservation transfers the trace to the complete independently checked C model. Native C independently checks262144 vector/pattern combinations; two actualC mutations must fail native assertions.",
            "limits": "Source handoff. All-instruction source/RTL event histories, actual OS response/interrupt binding, physical enable composition and warm/whole-machine histories remain separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("Native LDA handoff incomplete")


if __name__ == "__main__":
    qualify()
