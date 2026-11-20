"""Build and qualify an independent cycle-stepped NMOS reference CPU."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

from tools.validation.common import ROOT, digest, source_root, write_json

from .build import BBC, hashes
from .prepare import write_changed

SOURCE = ROOT / "tools/bbc/reference"
DIRECTORY = BBC / "reference-cycle"
BINARY = DIRECTORY / "cycle.node"
RECEIPT = DIRECTORY / "receipt.json"


def locked_assets():
    return json.loads((SOURCE / "lock.json").read_text())["assets"]


def derive(original):
    """Correct NMOS branch IRQ polls and retain NMI across long instructions.

    Unmodified chips loses the first IRQ poll on a page-crossing branch and
    carries a late pulse into a later instruction after a same-page branch.
    JS NMOS timing fixtures expose all four cases; beebjit's independent
    interp.c documents first-cycle / first-and-third-cycle polling. Preserve
    the original header/license; every replacement must match the pinned text.
    """

    def replace(before, after, count):
        nonlocal original
        if original.count(before) != count:
            raise ValueError("Unrecognized pinned CPU template: " + before)
        original = original.replace(before, after)

    replace("uint16_t irq_pip;", "uint16_t irq_pip;\n    uint8_t branch_irq;", 1)
    replace(
        "c->IR = _GD()<<3;",
        "c->IR = _GD()<<3;\n            c->branch_irq = !!((pins & M6502_IRQ) && !(c->P & M6502_IF));",
        1,
    )
    replace(
        "c->irq_pip>>=1;c->nmi_pip>>=1;", "c->irq_pip&=~0x100u;c->irq_pip>>=1;c->nmi_pip>>=1;", 8
    )
    for opcode in ["10", "30", "50", "70", "90", "B0", "D0", "F0"]:
        replace(
            f"case (0x{opcode}<<3)|3: c->PC=c->AD;_FETCH();break;",
            f"case (0x{opcode}<<3)|3: c->PC=c->AD;if(c->branch_irq)c->irq_pip|=0x200;_FETCH();break;",
            1,
        )
    # The pinned core widened nmi_pip to 32 bits but kept a 16-bit poll
    # mask. An edge on the preceding instruction can age to bit 16 during
    # seven-cycle indexed RMW and disappear at SYNC. Preserve all aged bits.
    replace("c->nmi_pip & 0xFC00", "c->nmi_pip & ~0x3FFu", 1)
    return original


def inputs():
    assets = locked_assets()
    for name, asset in assets.items():
        if digest(DIRECTORY / name) != asset["sha256"]:
            raise ValueError("Changed pinned reference asset: " + name)
    return {
        "files": hashes(
            [
                *SOURCE.glob("*"),
                Path(__file__),
                *map(lambda n: DIRECTORY / n, assets),
                DIRECTORY / "m6502.h",
                BBC / "6502_functional_test.bin",
                BBC / "cpu-irq.tsv",
                BBC / "cpu-nmi.tsv",
                ROOT / "tools/bbc/reference_bus.mjs",
                ROOT / "tools/bbc/reference_official.mjs",
            ]
        ),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
    }


def outputs():
    return hashes(
        [
            BINARY,
            DIRECTORY / "functional",
            DIRECTORY / "irq",
            DIRECTORY / "functional.log",
            DIRECTORY / "irq.log",
            DIRECTORY / "nmi.log",
            DIRECTORY / "reset",
            DIRECTORY / "reset.log",
            DIRECTORY / "bus",
            DIRECTORY / "bus.tsv",
            DIRECTORY / "bus.log",
            DIRECTORY / "reset-bus",
            DIRECTORY / "reset-bus.tsv",
            DIRECTORY / "official-bus",
            DIRECTORY / "official-bus.tsv",
            DIRECTORY / "official-inputs.tsv",
            DIRECTORY / "official-bus.log",
            DIRECTORY / "interrupt-bus",
            DIRECTORY / "irq-bus.tsv",
            DIRECTORY / "nmi-bus.tsv",
        ]
    )


def current():
    try:
        receipt = json.loads(RECEIPT.read_text())
        return (
            receipt.get("qualified") is True
            and receipt["inputs"] == inputs()
            and receipt["outputs"] == outputs()
            and len(receipt["outputs"]) == 20
        )
    except (OSError, ValueError, KeyError):
        return False


def prepare():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, asset in locked_assets().items():
        path = DIRECTORY / name
        if path.is_file() and digest(path) == asset["sha256"]:
            continue
        data = urllib.request.urlopen(asset["url"], timeout=60).read()
        import hashlib

        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("Reference download hash mismatch: " + name)
        write_changed(path, data)
    write_changed(
        DIRECTORY / "m6502.h", derive((DIRECTORY / "m6502-original.h").read_text()).encode()
    )
    if current():
        print("Verified unchanged cycle reference and CPU qualification", flush=True)
        return
    before = inputs()
    flags = ["c++", "-O3", "-std=c++17", "-iquote" + str(DIRECTORY)]
    subprocess.run(
        [*flags, "-shared", "-fPIC", str(SOURCE / "cycle.cpp"), "-o", str(BINARY)], check=True
    )
    for name, fixture in [("functional", "6502_functional_test.bin"), ("irq", "cpu-irq.tsv")]:
        subprocess.run(
            [*flags, str(SOURCE / (name + ".cpp")), "-o", str(DIRECTORY / name)], check=True
        )
        with (DIRECTORY / (name + ".log")).open("w") as log:
            subprocess.run(
                [str(DIRECTORY / name), str(BBC / fixture)],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=60,
            )
    subprocess.run([*flags, str(SOURCE / "reset.cpp"), "-o", str(DIRECTORY / "reset")], check=True)
    with (DIRECTORY / "reset.log").open("w") as log:
        subprocess.run(
            [str(DIRECTORY / "reset")], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30
        )
    with (DIRECTORY / "nmi.log").open("w") as log:
        subprocess.run(
            [str(DIRECTORY / "irq"), str(BBC / "cpu-nmi.tsv"), "nmi"],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=60,
        )
    subprocess.run([*flags, str(SOURCE / "bus.cpp"), "-o", str(DIRECTORY / "bus")], check=True)
    with (DIRECTORY / "bus.tsv").open("w") as fixture:
        subprocess.run([str(DIRECTORY / "bus")], stdout=fixture, check=True, timeout=30)
    with (DIRECTORY / "bus.log").open("w") as log:
        subprocess.run(
            [
                "node",
                str(ROOT / "tools/bbc/reference_bus.mjs"),
                str(source_root("jsbeeb")),
                str(DIRECTORY / "bus.tsv"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=30,
        )
    subprocess.run(
        [*flags, str(SOURCE / "reset_bus.cpp"), "-o", str(DIRECTORY / "reset-bus")], check=True
    )
    with (DIRECTORY / "reset-bus.tsv").open("w") as fixture:
        subprocess.run([str(DIRECTORY / "reset-bus")], stdout=fixture, check=True, timeout=30)
    subprocess.run(
        [*flags, str(SOURCE / "official_bus.cpp"), "-o", str(DIRECTORY / "official-bus")],
        check=True,
    )
    with (DIRECTORY / "official-bus.tsv").open("w") as fixture:
        subprocess.run(
            [str(DIRECTORY / "official-bus"), str(DIRECTORY / "official-inputs.tsv")],
            stdout=fixture,
            check=True,
            timeout=30,
        )
    with (DIRECTORY / "official-bus.log").open("w") as log:
        subprocess.run(
            [
                "node",
                str(ROOT / "tools/bbc/reference_official.mjs"),
                str(source_root("jsbeeb")),
                str(DIRECTORY / "official-inputs.tsv"),
                str(DIRECTORY / "official-bus.tsv"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=30,
        )
    subprocess.run(
        [*flags, str(SOURCE / "interrupt_bus.cpp"), "-o", str(DIRECTORY / "interrupt-bus")],
        check=True,
    )
    for signal in ["irq", "nmi"]:
        with (DIRECTORY / (signal + "-bus.tsv")).open("w") as fixture:
            subprocess.run(
                [str(DIRECTORY / "interrupt-bus"), str(BBC / ("cpu-" + signal + ".tsv")), signal],
                stdout=fixture,
                check=True,
                timeout=30,
            )
    if before != inputs():
        raise ValueError("Cycle reference sources changed during qualification")
    write_json(
        RECEIPT,
        {
            "qualified": True,
            "inputs": before,
            "outputs": outputs(),
            "model": "Pinned independent chips NMOS core with explicit branch IRQ and long-instruction NMI corrections; original BBC devices",
            "checks": [
                "Published Klaus $3469 functional program",
                "672 independent NMOS IRQ and 672 NMI timing fixtures",
                "Full seven-slot cold/warm NMOS RESET bus sequence and context",
                "348 complete NMOS bus fixtures; 180 absolute RMW cases cross-checked against original NMOS emulator",
                "9920 full bus traces for all 151 documented opcodes; architectural results cross-checked against original NMOS CPU; 9856 durations agree, original BRK retains its known 9-versus-7-clock difference",
            ],
        },
    )
    if not current():
        raise ValueError("Cycle reference qualification receipt failed")
    print(
        "Independent cycle CPU: complete Klaus functional program and 672 IRQ and 672 NMI fixtures passed",
        flush=True,
    )


if __name__ == "__main__":
    prepare()
