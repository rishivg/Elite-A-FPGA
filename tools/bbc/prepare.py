"""Prepare verified original ROMs and a pinned independent CPU functional test."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path

from tools.validation.common import ROOT, source_root
from tools.validation.setup import verify

from .cpu_bus import correct_bus

CPU_COMMIT = "e930327ffecc5062bfce70bbcfba2bbfa6de6e4c"
CORE_HASHES = {
    "cpu.v": "751b01f40377ad7fdf5c1c6a56a68f5b049373cd9c6adc5dc201b7392769d2f1",
    "ALU.v": "bf0caeb6c5960883bc96aadab4505d4a6bc2517ace471ae4313398cc0213bec3",
}
TEST_COMMIT = "7954e2dbb49c469ea286070bf46cdd71aeb29e4b"
TEST_HASH = "fa12bfc761e6f9057e4cc01a665a7b800ff01ae91f598af1e39a1201d01953fd"


def write_changed(path: Path, data: bytes):
    """Retain verified generated input mtimes when their contents are equal.

    Verilator's generated makefiles otherwise rebuild every CPU test even
    though the enabled core is identical. Verification still runs every time.
    """
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def enabled_cpu(source: str) -> str:
    """Add a true clock enable; upstream RDY changes its combinational address.

    Keep RDY asserted so synchronous memory addresses remain stable between
    enables. Gate clocked CPU state and ALU registers, while sampling NMI edges
    at the master clock. The caller first verifies the exact upstream hash.
    """
    source = source.replace("module cpu( clk,", "module bbc_cpu_ce( CE, clk,")
    # Export architectural observation through ports. Hierarchical references
    # work in Verilator but become undriven implicit wires in synthesis; PC
    # also supplies the externally visible reset dummy reads.
    header = "module bbc_cpu_ce( CE, clk, reset, AB, DI, DO, WE, IRQ, NMI, RDY );"
    if source.count(header) != 1:
        raise ValueError("Pinned CPU interface differs")
    source = source.replace(
        header,
        header.replace("RDY );", "RDY, BBC_PC, BBC_FETCH, BBC_A, BBC_X, BBC_Y, BBC_S, BBC_P );"),
    )
    source = source.replace(
        "input clk;",
        """output [15:0] BBC_PC;
output BBC_FETCH;
output [7:0] BBC_A, BBC_X, BBC_Y, BBC_S, BBC_P;
assign BBC_PC = PC;
assign BBC_FETCH = state == DECODE;
assign BBC_A = AXYS[0];
assign BBC_S = AXYS[1];
assign BBC_X = AXYS[2];
assign BBC_Y = AXYS[3];
assign BBC_P = {N,V,2'b11,D,I,Z,C};
input CE;              // Master-clock enable
input clk;""",
    )
    source = source.replace(".RDY(RDY) );", ".RDY(RDY && CE) );")
    source = source.replace("else if( RDY ) case( state )", "else if( RDY && CE ) case( state )")
    source = re.sub(
        r"(always @\(posedge clk\s*\)\s*\n)(?!\s*NMI_1|\s*if\( NMI_edge)",
        r"\1    if( CE || reset )\n",
        source,
    )
    source = source.replace(
        "if( NMI_edge && state == BRK3 )", "if( CE && NMI_edge && state == BRK3 )"
    )
    # NMOS selects/acknowledges NMI before the status-stack request. Capture
    # that decision in BRK1 for the later synchronous vector-address load.
    # An edge after this cutoff belongs to a subsequent interrupt, including
    # a second edge while an earlier NMI is entering its handler.
    vector = "NMI_edge ? 16'hfffa : 16'hfffe;"
    if source.count(vector) != 1:
        raise ValueError("Pinned CPU NMI vector selection differs")
    source = source.replace(vector, "bbc_nmi_vector ? 16'hfffa : 16'hfffe;")
    source = source.replace("reg NMI_edge = 0;", "reg bbc_nmi_vector = 0;\nreg NMI_edge = 0;")
    old = """always @(posedge clk )
    if( CE && NMI_edge && state == BRK3 )
        NMI_edge <= 0;
    else if( NMI & ~NMI_1 )
        NMI_edge <= 1;"""
    new = """always @(posedge clk)
    if( reset )
        bbc_nmi_vector <= 0;
    else if( CE && state == BRK1 )
        bbc_nmi_vector <= (NMI_edge || (NMI && !NMI_1)) && !res;

always @(posedge clk)
    if( reset )
        NMI_edge <= 0;
    else if( CE && state == BRK1 && !res )
        NMI_edge <= 0;
    else if( NMI & ~NMI_1 )
        NMI_edge <= 1;"""
    if old not in source:
        raise ValueError("Pinned CPU NMI logic differs")
    source = source.replace(old, new)
    # NMOS polls IRQ with the old I flag on the penultimate instruction cycle.
    # DECODE follows opcode fetch by one enabled clock. The penultimate
    # physical bus slot is three clocks before the next DECODE. Qualification
    # drives interrupts against the fetch slot, not this delayed diagnostic.
    source = source.replace(
        "reg bbc_nmi_vector = 0;",
        """reg bbc_irq_sample = 0;
reg bbc_irq_second = 0;
reg bbc_irq_polled = 0;
reg bbc_nmi_sample = 0;
reg bbc_nmi_second = 0;
reg bbc_nmi_polled = 0;
reg bbc_branch_nmi = 0;
reg bbc_branch_irq = 0;
reg bbc_hardware_interrupt = 0;
always @(posedge clk)
    if( reset ) begin
        bbc_irq_sample <= 0;
        bbc_irq_second <= 0;
        bbc_irq_polled <= 0;
        bbc_nmi_sample <= 0;
        bbc_nmi_second <= 0;
        bbc_nmi_polled <= 0;
        bbc_branch_nmi <= 0;
        bbc_branch_irq <= 0;
        bbc_hardware_interrupt <= 0;
    end else if( CE ) begin
        // PLP's following opcode and RTI's own poll must see restored I.
        // The synchronous stack read is already available in these states.
        bbc_irq_sample <= IRQ && !(((state == PULL2 && plp) || state == RTI2) ? DIMUX[2] : I);
        bbc_irq_second <= bbc_irq_sample;
        bbc_irq_polled <= bbc_irq_second;
        // Include an edge on this enabled clock, then retain the physical
        // poll phase through the same pipeline as IRQ. Master-clock capture
        // continues to preserve edges between CPU enables.
        bbc_nmi_sample <= NMI_edge || (NMI && !NMI_1);
        bbc_nmi_second <= bbc_nmi_sample;
        bbc_nmi_polled <= bbc_nmi_second;
        // Entry sets I here. Discard queued IRQ samples taken with its old
        // value, so the first handler opcode cannot re-enter the same IRQ.
        if( state == BRK3 ) begin
            bbc_irq_sample <= 0;
            bbc_irq_second <= 0;
            bbc_irq_polled <= 0;
        end
        // Entry has no interrupt poll. Retain late physical edges separately,
        // then resample them so the first handler instruction still executes.
        if( state == BRK3 ) begin
            bbc_nmi_sample <= 0;
            bbc_nmi_second <= 0;
            bbc_nmi_polled <= 0;
            bbc_branch_nmi <= 0;
        end
        if( state == DECODE ) begin
            bbc_branch_irq <= bbc_irq_sample;
            bbc_branch_nmi <= bbc_nmi_sample;
            bbc_hardware_interrupt <= bbc_irq_polled || bbc_nmi_polled;
        end
        // Taken NMOS branches without page crossing poll on their first
        // cycle. Crossing branches additionally poll before the last cycle.
        if( state == BRA1 && !(CO ^ backwards) ) begin
            bbc_irq_polled <= bbc_branch_irq;
            bbc_nmi_polled <= bbc_branch_nmi;
        end
        if( state == BRA2 ) begin
            bbc_irq_polled <= bbc_branch_irq || bbc_irq_second;
            bbc_nmi_polled <= bbc_branch_nmi || bbc_nmi_second;
        end
    end

reg bbc_nmi_vector = 0;""",
    )
    source = source.replace("(~I & IRQ)", "bbc_irq_polled")
    source = source.replace("(IRQ & ~I)", "bbc_irq_polled")
    source = source.replace("bbc_irq_polled | NMI_edge", "bbc_irq_polled | bbc_nmi_polled")
    source = source.replace(
        "(IRQ | NMI_edge) ? (P & 8'b1110_1111) : P",
        "bbc_hardware_interrupt ? (P & 8'b1110_1111) : P",
    )
    # NMOS reset reads the three stack addresses; it never writes a frame.
    # Upstream reuses BRK entry for reset and leaves WE asserted on those
    # cycles, silently corrupting RAM before the reset vector is fetched.
    before = "BRK0,   // writing to stack or memory\n        BRK1,\n        BRK2,\n        JSR0,"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU reset write decode differs")
    source = source.replace(
        before, "BRK0,\n        BRK1,\n        BRK2:    WE = !res;\n\n        JSR0,"
    )
    # NMOS RMW writes the old operand during its ALU cycle, then the new
    # result. Replacing the first write with a read loses MMIO side effects.
    before = "WRITE:   DO = ADD;"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU RMW data decode differs")
    source = source.replace(before, "READ:    DO = DIMUX;\n\n        " + before)
    before = "PUSH1,\n        WRITE:   WE = 1;"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU RMW write decode differs")
    source = source.replace(before, "PUSH1,\n        READ,\n        WRITE:   WE = 1;")
    # Indexed zero-page modes dummy-read the unindexed zero-page address,
    # rather than the following instruction byte. Their ALU inputs retain
    # the preceding operand on the synchronous-memory interface.
    before = "ZP0,\n        INDY0:          AB = { ZEROPAGE, DIMUX };"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU zero-page address decode differs")
    source = source.replace(before, "ZPX0,\n        INDX0,\n        " + before)
    # PLP's I flag must be available for the following instruction's poll;
    # retain the old I during PLP's own penultimate-cycle poll.
    source = source.replace(
        "else if( state == DECODE )\n        if( plp ) I <= ADD[2];",
        "else if( state == PULL2 && plp )\n        I <= DIMUX[2];",
    )
    # CLI/SEI's I update belongs on their completing DECODE edge, before the
    # next opcode fetch. Its own penultimate poll has sampled the old I.
    before = "else if( state == REG ) begin\n        if( sei ) I <= 1;\n        if( cli ) I <= 0;"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU I flag decode differs")
    source = source.replace(
        before,
        "else if( state == DECODE ) begin\n        if( IR == 8'h78 ) I <= 1;\n        if( IR == 8'h58 ) I <= 0;",
    )
    # Preserve PC while RESET is held. A stale microcode state must not
    # increment it on the asserting edge, before the reset state settles.
    before = "if( RDY )\n        PC <= PC_temp + PC_inc;"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU PC update differs")
    source = source.replace(before, "if( RDY && !reset )\n        PC <= PC_temp + PC_inc;")
    before = "ALU ALU( .clk(clk),"
    if source.count(before) != 1:
        raise ValueError("Pinned CPU ALU instance differs")
    source = source.replace(before, "bbc_alu ALU( .clk(clk),")
    return (
        "// Generated by tools.bbc.prepare from verified unchanged Arlet source.\n"
        + warm_reset_support(correct_bus(source))
    )


def warm_reset_support(source: str) -> str:
    """Accept a digital restart request at SYNC without losing a pending commit.

    The two visible dummy reads finish the older instruction's pipeline before
    entering the existing reset stack reads. The reference pin engine accepts
    RES at SYNC; this interface does not model a physical held RESET pin.
    """

    def replace(before, after):
        nonlocal source
        if source.count(before) != 1:
            raise ValueError("Pinned CPU warm-reset anchor differs: " + before)
        source = source.replace(before, after)

    replace("BBC_P );", "BBC_P, BBC_SYNC, BBC_WARM, BBC_WARM_BEGIN );")
    replace(
        "assign BBC_FETCH = state == DECODE;",
        """assign BBC_FETCH = state == DECODE;
output BBC_SYNC;
// These states drive an opcode fetch on the physical bus. DECODE is one
// enabled edge later on this synchronous-memory interface. RTS3 is a dummy.
assign BBC_SYNC = state == FETCH || state == REG || state == PUSH1 ||
    state == PULL2 || state == RTI4 || state == JMP1 || state == BRA2 ||
    (state == BRA0 && !cond_true) || (state == BRA1 && !(CO ^ backwards));""",
    )
    replace("input CE;", "input BBC_WARM, BBC_WARM_BEGIN;\ninput CE;")
    # PUSH1 and PULL2 issue the real opcode fetch. The BBC response latch
    # holds its byte for DECODE, including when the stack write changed it.
    replace("IRHOLD_valid ? IRHOLD : DIMUX;", "(IRHOLD_valid && state != DECODE) ? IRHOLD : DIMUX;")
    replace(
        "else if( RDY && CE ) case( state )",
        "else if( CE && BBC_WARM ) state <= BRK0;\n    else if( RDY && CE ) case( state )",
    )
    replace("if( RDY && !reset )", "if( RDY && !reset && !BBC_WARM )")
    replace("if( reset )\n         res <= 1;", "if( reset || BBC_WARM )\n         res <= 1;")
    # Keep the previous operand/ALU result intact. Only the discarded next
    # opcode decode becomes NOP; the older register and flag commit still runs.
    replace("assign IR = bbc_irq_polled", "assign IR = BBC_WARM ? 8'hea : bbc_irq_polled")
    replace(
        "if( reset ) begin\n        bbc_irq_sample",
        "if( reset || (CE && BBC_WARM) ) begin\n        bbc_irq_sample",
    )
    replace(
        "if( reset )\n        bbc_nmi_vector",
        "if( reset || (CE && BBC_WARM) )\n        bbc_nmi_vector",
    )
    replace(
        "reg NMI_edge = 0;       // captured NMI edge",
        """// RES consumes an aged NMI at SYNC but retains newer edges. Track
// those edges separately so a second edge cannot merge into the consumed one.
reg bbc_nmi_unaged = 0, bbc_nmi_recent = 0, bbc_nmi_recent2 = 0;
always @(posedge clk)
    if( reset ) bbc_nmi_unaged <= 0;
    else if( CE ) bbc_nmi_unaged <= 0;
    else if( NMI && !NMI_1 ) bbc_nmi_unaged <= 1;
always @(posedge clk)
    if( reset ) begin bbc_nmi_recent <= 0; bbc_nmi_recent2 <= 0; end
    else if( CE ) begin
        // Interrupt entry has no final poll. Retained edges become recent
        // again at its last vector read, before the first handler opcode.
        bbc_nmi_recent <= ((state == JMP0 && (res || brk)) && NMI_edge) ||
                          bbc_nmi_unaged || (NMI && !NMI_1);
        bbc_nmi_recent2 <= bbc_nmi_recent;
    end
reg NMI_edge = 0;       // captured NMI edge""",
    )
    replace(
        "if( reset )\n        NMI_edge <= 0;",
        """if( reset )
        NMI_edge <= 0;
    else if( CE && BBC_WARM_BEGIN )
        NMI_edge <= bbc_nmi_recent || bbc_nmi_unaged || (NMI && !NMI_1) ||
                    ((state == BRA1 && !(CO ^ backwards)) && bbc_nmi_recent2);""",
    )
    return source


def nmos_alu(source: str) -> str:
    """Derive NMOS overflow from operand signs and the unadjusted result sign.

    Upstream includes decimal carry correction in its overflow XOR. Decimal
    carry is not a binary carry into bit 7, so that expression missets V for
    valid and invalid BCD operands. Keep the original registered operand/result
    signs and every data, carry, clock-enable and pipeline path unchanged.
    """
    for before, after in [
        ("module ALU(", "module bbc_alu("),
        ("assign V = AI7 ^ BI7 ^ CO ^ N;", "assign V = ~(AI7 ^ BI7) & (AI7 ^ N);"),
    ]:
        if source.count(before) != 1:
            raise ValueError("Pinned ALU overflow decode differs")
        source = source.replace(before, after)
    return source


def main(cpu_fixtures=False):
    verification = verify()
    directory = ROOT / "build/bbc"
    directory.mkdir(parents=True, exist_ok=True)
    emulator = source_root("jsbeeb")
    font = subprocess.check_output(
        ["node", str(ROOT / "tools/bbc/reference_teletext_font.mjs"), str(emulator), "/dev/stdout"]
    )
    if len(font.splitlines()) != 960:
        raise ValueError("Incomplete original teletext font")
    write_changed(directory / "teletext-font.hex", font)
    roms = [
        emulator / "public/roms/os.rom",
        emulator / "public/roms/BASIC.ROM",
        emulator / "public/roms/b/DFS-1.2.rom",
    ]
    data = b"".join(p.read_bytes() for p in roms)
    if len(data) != 49152:
        raise ValueError("Original ROM lengths differ")
    write_changed(directory / "rom.hex", "".join(f"{b:02x}\n" for b in data).encode())
    disc = source_root("elite_a") / "5-compiled-game-discs/elite-a-released.ssd"
    write_changed(directory / "disc.ssd", disc.read_bytes())
    # Physical matrix transitions, consumed by the independent RTL keyboard.
    keys = {
        "*": (8, 4, True),
        "E": (2, 2, False),
        "X": (2, 4, False),
        "C": (2, 5, False),
        " ": (2, 6, False),
        "!": (0, 3, True),
        "B": (4, 6, False),
        "O": (6, 3, False),
        "T": (3, 2, False),
        "\r": (9, 4, False),
    }
    events = []
    for i, char in enumerate("*EXEC !BOOT\r"):
        col, row, shift = keys[char]
        start = 32000000 + i * 3200000
        for c, r in ([(0, 0)] if shift else []) + [(col, row)]:
            events.extend([(start, c, r, 1), (start + 1600000, c, r, 0)])
    for start, col, row in [(384000000, 5, 5), (448000000, 2, 6), (544000000, 6, 7)]:
        events.extend([(start, col, row, 1), (start + 3200000, col, row, 0)])
    write_changed(
        directory / "boot.keys", "".join("%d %d %d %d\n" % e for e in sorted(events)).encode()
    )
    core = ROOT / "third_party/6502"
    for name, expected in CORE_HASHES.items():
        if hashlib.sha256((core / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Modified upstream CPU: {name}")
    enabled = (
        enabled_cpu((core / "cpu.v").read_text()) + "\n" + nmos_alu((core / "ALU.v").read_text())
    )
    write_changed(directory / "ArletCpuEnabled.v", enabled.encode())
    functional = directory / "6502_functional_test.bin"
    if not functional.exists():
        url = f"https://raw.githubusercontent.com/Klaus2m5/6502_65C02_functional_tests/{TEST_COMMIT}/bin_files/6502_functional_test.bin"
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != TEST_HASH:
            raise ValueError("CPU test download checksum mismatch")
        functional.write_bytes(payload)
    if hashlib.sha256(functional.read_bytes()).hexdigest() != TEST_HASH:
        raise ValueError("CPU functional test modified")
    metadata = {
        "cpu_commit": CPU_COMMIT,
        "core_hashes": CORE_HASHES,
        "functional_test_commit": TEST_COMMIT,
        "functional_test_sha256": TEST_HASH,
        "enabled_core_sha256": hashlib.sha256(enabled.encode()).hexdigest(),
        "reference_verification": verification,
    }
    write_changed(directory / "verification.json", (json.dumps(metadata, indent=2) + "\n").encode())
    if cpu_fixtures:
        for kind in ["irq", "nmi"]:
            subprocess.run(
                [
                    "node",
                    str(ROOT / "tools/bbc/cpu_timing.mjs"),
                    str(emulator),
                    str(directory / f"cpu-{kind}.tsv"),
                    kind,
                ],
                check=True,
            )
    print("Verified original OS/BASIC/DFS ROMs and independent NMOS CPU test")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-fixtures", action="store_true")
    main(parser.parse_args().cpu_fixtures)
