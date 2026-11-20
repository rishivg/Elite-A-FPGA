"""Derive the machine CPU's explicit NMI event port from the level-input CPU."""

import re
from pathlib import Path

from tools.validation.common import ROOT

DIRECTORY = ROOT / "build/bbc"
CORE = DIRECTORY / "ArletCpuEvents.v"
WRAPPER = DIRECTORY / "BbcCpuEvents.v"


def derive(core, wrapper):
    pattern = r"NMI\s*(?:&&|&)\s*[!~]NMI_1"
    if len(re.findall(pattern, core)) != 6:
        raise ValueError("Changed maintained NMI edge consumers")
    core = re.sub(pattern, lambda match: "(" + match[0] + " || BBC_NMI_EVENT)", core)
    header = core.index(");")
    core = core[:header] + ", BBC_NMI_EVENT" + core[header:]
    header = core.index(");") + 2
    core = core[:header] + "\ninput BBC_NMI_EVENT;\n" + core[header:]
    core = re.sub(r"\bbbc_cpu_ce\b", "bbc_cpu_events", core)
    core = re.sub(r"\bbbc_alu\b", "bbc_event_alu", core)
    if wrapper.count("module BbcCpu(") != 1 or wrapper.count(".BBC_P(flags));") != 1:
        raise ValueError("Changed maintained CPU wrapper")
    wrapper = wrapper.replace("module BbcCpu(", "module BbcCpuEvents(\n    input wire nmi_event,")
    wrapper = wrapper.replace("bbc_cpu_ce core(", "bbc_cpu_events core(")
    wrapper = wrapper.replace(".BBC_P(flags));", ".BBC_NMI_EVENT(nmi_event), .BBC_P(flags));")
    return core, wrapper


def expected():
    return derive(
        (DIRECTORY / "ArletCpuEnabled.v").read_text(), (ROOT / "rtl/bbc/BbcCpu.v").read_text()
    )


def prepare():
    for path, text in zip((CORE, WRAPPER), expected(), strict=True):
        if not path.exists() or path.read_text() != text:
            path.write_text(text)


def audit():
    if any(
        not p.is_file() or p.read_text() != text
        for p, text in zip((CORE, WRAPPER), expected(), strict=True)
    ):
        raise ValueError("Machine CPU event derivative is stale")


def files():
    return [Path(__file__), CORE, WRAPPER]


def inline(source):
    """Keep native/tool readers self-contained; Scala simulation uses RTL paths."""
    audit()
    if re.search(r"\bBbcCpuEvents\s+cpu\s*\(", source) is None:
        return source
    if "module BbcCpuEvents(" in source:
        raise ValueError("Machine CPU event derivative already inlined")
    return source + "\n" + CORE.read_text() + "\n" + WRAPPER.read_text()


def inline_file(path):
    source = path.read_text()
    text = inline(source)
    if text != source:
        path.write_text(text)


def strip(source):
    audit()
    for name in ("BbcCpuEvents", "bbc_cpu_events", "bbc_event_alu"):
        if len(re.findall(r"^module\s+" + name + r"\b", source, re.M)) != 1:
            raise ValueError("Missing or duplicate actual CPU event module")
    for text in (CORE.read_text(), WRAPPER.read_text()):
        if source.count(text) != 1:
            raise ValueError("Actual machine does not bind the exact CPU event derivative")
        source = source.replace(text, "")
    return source


def observed(ports):
    from tools.bbc import cpu_interrupt_proof

    audit()
    return cpu_interrupt_proof.observed(CORE.read_text(), WRAPPER.read_text(), ports=ports)


if __name__ == "__main__":
    prepare()
    audit()
