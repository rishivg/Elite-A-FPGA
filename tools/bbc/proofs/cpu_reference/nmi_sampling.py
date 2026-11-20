"""Check physical NMI capture against the maintained C and original JS sources."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.cpu_reference import foundation, frontend, prepared
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/cpu-source-nmi-sampling"
HELPER = ROOT / "tools/bbc/reference/nmi.h"


def detector(source):
    start = source.index("uint64_t m6502_tick(m6502_t* c, uint64_t pins) {")
    tick = source[start:]
    match = re.search(
        r"if \(0 != \(\(pins & \(pins \^ c->PINS\)\) & M6502_NMI\)\) \{\s*"
        r"c->nmi_pip \|= 0x100;\s*\}",
        tick,
    )
    if match is None:
        raise ValueError("Changed maintained NMI detector")
    # Sampling updates only the stored NMI level. The remainder of tick must
    # overwrite PINS before returning and never read its preceding value.
    if re.findall(r"c->PINS[^;]*;", tick) != [
        "c->PINS)) & M6502_NMI)) {\n            c->nmi_pip |= 0x100;",
        "c->PINS = pins;",
        "c->PINS = pins;",
    ]:
        raise ValueError("New stored-pin consumer or return path needs a sampling proof")
    return match[0]


def harness(fragment):
    equal = " && ".join(
        f"left.{name} == right.{name}" for name, _ in frontend.FIELDS if name != "PINS"
    )
    fields = " && ".join(
        f"sampled.{name} == initial.{name}"
        for name, _ in frontend.FIELDS
        if name not in ("PINS", "nmi_pip")
    )
    return f"""#define CHIPS_IMPL
#include "m6502.h"
#include "nmi.h"
#include <assert.h>
m6502_t nondet_state(void);
uint64_t nondet_pins(void);
bool nondet_level(void);
static void source_detector(m6502_t *c, uint64_t pins) {{ {fragment} }}
int main(void) {{
    m6502_t initial = nondet_state(), sampled = initial;
    bool level = nondet_level(), second = nondet_level();
    bbc_sample_nmi(&sampled, level);
    uint32_t expected = initial.nmi_pip |
        ((level && !(initial.PINS & M6502_NMI)) ? 0x100u : 0u);
    assert(sampled.nmi_pip == expected);
    assert(sampled.PINS == ((initial.PINS & ~M6502_NMI) | (level ? M6502_NMI : 0)));
    assert({fields});
    bbc_sample_nmi(&sampled, second);
    assert(sampled.nmi_pip == (expected | ((second && !level) ? 0x100u : 0u)));
    uint64_t pins = nondet_pins();
    m6502_t left = initial, right = initial;
    source_detector(&left, pins);
    bbc_sample_nmi(&right, !!(pins & M6502_NMI));
    source_detector(&right, pins);
    assert({equal});
    assert((left.PINS & ~M6502_NMI) == (right.PINS & ~M6502_NMI));
    return 0;
}}
"""


def qualify():
    parent_path = foundation.DIRECTORY / "result.json"
    memory_composition.sealed(parent_path)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    header = ROOT / "build/bbc/reference-cycle/m6502.h"
    original_js = source_root("jsbeeb") / "src/6502.js"
    sampler = Path(__file__).with_name("nmi_sample.v")
    full_model = foundation.DIRECTORY / "source.v"
    js_checker = Path(__file__).with_suffix(".mjs")
    cbmc = Path(shutil.which("cbmc") or foundation.DEFAULT_CBMC).resolve(strict=True)
    files = [
        Path(__file__),
        js_checker,
        HELPER,
        sampler,
        full_model,
        ROOT / "tools/bbc/reference/cycle.cpp",
        ROOT / "tools/bbc/reference_cycle.mjs",
        header,
        original_js,
        parent_path,
        Path(frontend.__file__),
        Path(prepared.__file__),
        Path(memory_composition.__file__),
        Path(__file__).parents[1] / "video_cursor.py",
        ROOT / "tools/validation/common.py",
        cbmc,
        Path(shutil.which("node")),
        Path(shutil.which("gcc")),
        Path(shutil.which("yosys")),
    ]
    before = hashes(files)
    fragment = detector(header.read_text())
    offsets, cursor = {}, 256
    for name, width in frontend.FIELDS:
        cursor -= width
        offsets[name] = (cursor + width - 1, cursor)
    if cursor or offsets["PINS"] != (167, 104) or offsets["nmi_pip"] != (79, 48):
        raise ValueError("Changed packed sampling ABI")
    projection = DIRECTORY / "projection.v"
    projection.write_text("""module NmiSamplingProjection(output equivalent);
(* anyseq *) reg [255:0] state;
(* anyseq *) reg nmi;
wire [255:0] sampled, following;
wire [63:0] nextPins;
wire trap;
wire [63:0] pins = (state[167:104] & ~64'hc000000) |
                  64'h11000000 | ({63'd0, nmi} << 27);
M6502NmiSample sample(.state(state), .nmi(nmi), .sampled(sampled));
M6502ReferenceNext source(.state_in(state), .pins_in(pins),
    .state_out(following), .pins_out(nextPins), .trap(trap));
wire [255:0] changed = (256'hffffffff << 48) | (256'd1 << 131);
assign equivalent = !trap && sampled[79:48] == following[79:48] &&
    sampled[131] == following[131] &&
    ((sampled & ~changed) == (state & ~changed));
endmodule
""")
    graph = DIRECTORY / "projection.json"
    prepared.prepare(
        DIRECTORY,
        f"read_verilog -formal -sv {full_model} {sampler} {projection}; prep -top NmiSamplingProjection -flatten; check -assert; opt; write_json {graph}",
        [full_model, sampler, projection],
        [graph],
    )
    with (DIRECTORY / "projection.log").open("w") as output:
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
    if trial.returncode or "SUCCESS!" not in (DIRECTORY / "projection.log").read_text():
        raise ValueError("Packed physical sampler differs from complete C NMI hold semantics")
    checks = []
    for name, body, positive in (
        ("positive", HELPER.read_text(), True),
        (
            "lost-edge",
            HELPER.read_text().replace("c->nmi_pip |= 0x100;", "c->nmi_pip |= 0;"),
            False,
        ),
        (
            "level-instead-of-edge",
            HELPER.read_text().replace("(pins & (pins ^ c->PINS))", "pins"),
            False,
        ),
        (
            "lost-previous-level",
            HELPER.read_text().replace("c->PINS = pins;", "c->PINS &= ~M6502_NMI;"),
            False,
        ),
    ):
        case = DIRECTORY / name
        case.mkdir(exist_ok=True)
        (case / "nmi.h").write_text(body)
        (case / "m6502.h").write_text(header.read_text())
        program, log = case / "check.c", case / "proof.log"
        program.write_text(harness(fragment))
        with log.open("w") as output:
            trial = subprocess.run(
                [
                    str(cbmc),
                    str(program),
                    "--unwind",
                    "1",
                    "--unwinding-assertions",
                    "--bounds-check",
                    "--pointer-check",
                    "--signed-overflow-check",
                    "--slice-formula",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
        text = log.read_text()
        accepted = (
            trial.returncode == 0 and "VERIFICATION SUCCESSFUL" in text
            if positive
            else trial.returncode == 10
            and re.search(r"\[main[.]assertion[.][0-9]+\].*: FAILURE", text) is not None
            and "VERIFICATION FAILED" in text
        )
        checks.append({"name": name, "accepted": accepted})
        print("NMI sampling " + name + (": PASS" if accepted else ": FAIL"), flush=True)
    native = DIRECTORY / "native.c"
    native.write_text("""#define CHIPS_IMPL
#include "m6502.h"
#include "nmi.h"
#include <stdio.h>
int main(void) {
    for (unsigned initial = 0; initial < 2; ++initial)
      for (unsigned retained = 0; retained < 2; ++retained)
        for (unsigned pattern = 0; pattern < 16; ++pattern) {
            m6502_t c = {0};
            c.PINS = initial ? M6502_NMI : 0;
            c.nmi_pip = retained ? 0x100 : 0;
            printf("%u %u %u", initial, retained, pattern);
            for (unsigned bit = 0; bit < 4; ++bit) {
                bbc_sample_nmi(&c, !!(pattern & (1u << bit)));
                printf(" %u", !!(c.nmi_pip & 0x100));
            }
            puts("");
        }
}
""")
    shutil.copyfile(header, DIRECTORY / "m6502.h")
    shutil.copyfile(HELPER, DIRECTORY / "nmi.h")
    executable, trace = DIRECTORY / "native", DIRECTORY / "native.tsv"
    subprocess.run(["gcc", "-O2", str(native), "-o", str(executable)], check=True, timeout=60)
    trace.write_text(subprocess.check_output([str(executable)], text=True))
    original_result = DIRECTORY / "original-js.json"
    subprocess.run(
        ["node", str(js_checker), str(source_root("jsbeeb")), str(trace), str(original_result)],
        check=True,
        timeout=30,
    )
    js_result = json.loads(original_result.read_text())
    memory_composition.sealed(parent_path)
    stable = before == hashes(files)
    passed = stable and all(check["accepted"] for check in checks) and js_result["passed"]
    artifacts = [
        projection,
        graph,
        DIRECTORY / "projection.log",
        DIRECTORY / "prepared.json",
        DIRECTORY / "prepare.log",
        native,
        executable,
        trace,
        original_result,
        DIRECTORY / "m6502.h",
        DIRECTORY / "nmi.h",
        *[
            path
            for check in checks
            for path in (DIRECTORY / check["name"]).glob("*")
            if path.is_file()
        ],
    ]
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": passed,
            "inputs_unchanged": stable,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "checks": checks,
            "original_js": js_result,
            "scope": "All-state native C sampling preserves every instruction/profile field and pipeline age, retains only physical NMI rises, and updates only stored NMI level. Fresh CBMC checks the helper and algebraic commutation with the exact maintained C detector; every later stored-pin consumer is guarded. Four-edge native traces match original JS setNmi at every edge for all 64 initial-level/latch/pattern combinations. Three actual helper faults must fail.",
            "limits": "Establishes the independent reference sampling semantics, not the RTL/source event relation, device callback ordering, warm reset or whole-machine equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not passed:
        raise ValueError("NMI sampling bridge incomplete")


if __name__ == "__main__":
    qualify()
