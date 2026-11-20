"""Reuse formal stream results only when their complete sources, artifacts and controls match."""

import argparse
import importlib
import json
import subprocess

from tools.bbc import build
from tools.validation.common import ROOT

PROFILES = {
    "cpu-interrupt-stream-proof": (
        "interrupt_induction",
        {},
        42,
        (
            "positive",
            "old-decimal",
            "inverted-bpl",
            "wrong-hardware-b",
            "old-nmi-poll",
            "late-vector-selection",
            "late-nmi-ack",
        ),
    ),
    "cpu-documented-irq-stream-proof": (
        "interrupt_induction",
        {"all_documented": True, "irq_only": True},
        151,
        ("positive", "old-decimal", "inverted-bpl", "wrong-hardware-b"),
    ),
    "cpu-documented-interrupt-stream-proof": (
        "interrupt_induction",
        {"all_documented": True},
        151,
        (
            "positive",
            "old-decimal",
            "inverted-bpl",
            "wrong-hardware-b",
            "old-nmi-poll",
            "late-vector-selection",
            "late-nmi-ack",
        ),
    ),
    "cpu-immediate-induction-proof": ("immediate_induction", {}, 11, ("positive", "old-decimal")),
    "cpu-two-cycle-induction-proof": (
        "immediate_induction",
        {"two_cycle": True},
        33,
        ("positive", "old-decimal"),
    ),
    "cpu-branch-induction-proof": (
        "branch_induction",
        {},
        41,
        ("positive", "old-decimal", "inverted-bpl"),
    ),
    "cpu-direct-memory-induction-proof": (
        "memory_induction",
        {},
        71,
        ("positive", "old-decimal", "inverted-bpl", "omitted-store"),
    ),
    "cpu-memory-induction-proof": (
        "memory_induction",
        {"all_memory": True},
        141,
        ("positive", "old-decimal", "inverted-bpl", "omitted-store", "omitted-rmw-write"),
    ),
    "cpu-documented-induction-proof": (
        "documented_induction",
        {},
        151,
        (
            "positive",
            "old-decimal",
            "inverted-bpl",
            "omitted-store",
            "omitted-rmw-write",
            "brk-saved-b-clear",
        ),
    ),
}


def current(profile, report):
    """Validate the actual sealed proofs, not just a success flag or timestamps."""
    module, options, count, controls = PROFILES[profile]
    if (
        not build.generation_current()
        or report.get("passed") is not True
        or report.get("inputs_unchanged") is not True
        or report.get("instruction_count") != "unbounded"
        or report.get("enable_period") != 1
        or report.get("universal_cpu_equivalence") is not False
        or report.get("universal_game_equivalence") is not False
        or len(set(report.get("opcodes", []))) != count
        or report.get("admitted_opcode_domains") != count**2
        or report.get("nonempty_domains") is not True
        or [p.get("name") for p in report.get("proofs", [])] != list(controls)
        or any(p.get("accepted") is not True for p in report.get("proofs", []))
    ):
        return False
    version = subprocess.check_output(["yosys", "-V"], text=True).strip()
    if report.get("tools", {}).get("yosys") != version:
        return False
    for category in ("inputs", "artifacts"):
        files = report.get(category, {})
        if not files or files != build.hashes([ROOT / path for path in files]):
            return False
    driver = "tools/bbc/proofs/" + module + ".py"
    if driver not in report["inputs"]:
        return False
    directory = build.BBC / profile
    logs = [(directory / (control + ".log")).read_text() for control in controls]
    forbidden = ("multiple conflicting drivers", "Driver-driver conflict", "used but has no driver")
    if any(problem in log for log in logs for problem in forbidden):
        return False
    if "Induction step proven: SUCCESS!" not in logs[0]:
        return False
    for control in controls[1:]:
        if (
            "SAT temporal induction proof finished - model found for base case: FAIL!"
            not in (directory / (control + ".log")).read_text()
        ):
            return False
    graph = json.loads((directory / "positive.json").read_text())
    if len(graph["modules"]) != 1:
        return False
    top = next(iter(graph["modules"]))
    if module == "interrupt_induction":
        nmi = not options.get("irq_only", False)
        if (
            report.get("arbitrary_irq_after_prefix") is not True
            or report.get("arbitrary_nmi_after_prefix") is not nmi
        ):
            return False
        body = graph["modules"][top]
        for pin in ("irqPin", *(("nmiPin",) if nmi else ())):
            bits = body["netnames"].get(pin, {}).get("bits", [])
            if len(bits) != 1 or not any(
                cell["type"] == "$anyseq" and cell["connections"].get("Y") == bits
                for cell in body["cells"].values()
            ):
                return False
    guard = importlib.import_module(
        "tools.bbc.proofs." + ("immediate_induction" if count <= 33 else "branch_induction")
    )
    try:
        if count <= 33:
            guard.guard_domains(graph)
        else:
            guard.guard_domains(graph, top)
    except ValueError:
        return False
    domains = (directory / "domains.log").read_text()
    if domains.count("SAT solving finished - model found:") != count**2:
        return False
    if count >= 41 and (
        report.get("excluded_opcode_positions") != (256 - count) * 2
        or domains.count("SAT solving finished - no model found.") != (256 - count) * 2
    ):
        return False
    return True


def qualify(profile):
    module, options, count, _ = PROFILES[profile]
    path = build.BBC / profile / "result.json"
    if path.exists():
        try:
            report = json.loads(path.read_text())
            if current(profile, report):
                print(f"Verified cached unbounded stream proof: {count} opcodes", flush=True)
                return report
        except (OSError, ValueError, KeyError):
            pass
    return importlib.import_module("tools.bbc.proofs." + module).qualify(**options)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILES)
    qualify(parser.parse_args().profile)
