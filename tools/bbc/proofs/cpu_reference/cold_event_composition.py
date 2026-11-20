"""Join the original and RTL cold CPU histories at their shared bus model."""

import re
from pathlib import Path

from tools.bbc.proofs import current_machine, memory_composition, nmi_macro
from tools.bbc.proofs.cpu_reference import (
    cold_lda_event_history,
    documented_model,
    event_history,
    foundation,
    lda_handoff,
    reset_release,
    reset_rtl,
    rtl_event_history,
)
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, source_root, write_json

BBC = ROOT / "build/bbc"
DIRECTORY = BBC / "cpu-cold-event-composition"


def shared_property(text):
    properties = re.findall(r"assign observedEquivalent=[\s\S]+?;", text)
    if len(properties) != 1:
        raise ValueError("Missing shared bus and retirement property")
    return properties[0]


def qualify():
    parents = current_machine.audit()
    core, wrapper = BBC / "ArletCpuEnabled.v", ROOT / "rtl/bbc/BbcCpu.v"
    paths = {
        "current_parents": current_machine.DIRECTORY / "result.json",
        "foundation": foundation.DIRECTORY / "result.json",
        "cold_source": lda_handoff.DIRECTORY / "result.json",
        "source_history": event_history.DIRECTORY / "result.json",
        "rtl_history": cold_lda_event_history.DIRECTORY / "result.json",
        "startup_bus": reset_rtl.DIRECTORY / "result.json",
        "reset_release": reset_release.DIRECTORY / "result.json",
        "dummy_responses": BBC / "cpu-reset-read-bridge/result.json",
        "waits": BBC / "cpu-nmi-idle-summary/result.json",
        "accepted_edges": BBC / "cpu-nmi-macro-transition-proof/result.json",
    }
    evidence = {name: memory_composition.sealed(path) for name, path in paths.items()}
    required = [
        "rom",
        "memory-routing",
        "startup-interrupts",
        "request-spacing",
        "response-deadline",
    ]
    parent_paths = [current_machine.DIRECTORY / part / "result.json" for part in required]
    os_rom = source_root("jsbeeb") / "public/roms/os.rom"
    inputs = [
        Path(__file__),
        *paths.values(),
        *parent_paths,
        os_rom,
        *[
            Path(module.__file__)
            for module in (
                cold_lda_event_history,
                documented_model,
                event_history,
                nmi_macro,
                rtl_event_history,
                memory_composition,
                current_machine,
            )
        ],
        core,
        wrapper,
    ]
    before = hashes(inputs)
    if not {str(path.relative_to(ROOT)) for path in parent_paths} <= set(
        evidence["current_parents"]["premises"]
    ):
        raise ValueError("Missing actual parent startup premises")
    for path in parent_paths:
        report = memory_composition.sealed(path)
        for _, rtl, _ in current_machine.parents():
            recorded = report["inputs"]
            if recorded.get(str(rtl.relative_to(ROOT)), recorded.get(str(rtl))) != digest(rtl):
                raise ValueError("Startup premise describes another parent")
    image = os_rom.read_bytes()
    vector = int.from_bytes(image[0x3FFC:0x3FFE], "little")
    if (
        evidence["startup_bus"]["os_vector"] != vector
        or image[vector - 0xC000 : vector - 0xC000 + 3] != b"\xa9\x40\x8d"
    ):
        raise ValueError("OS startup vector or instruction bytes changed")
    for name in ("startup_bus", "dummy_responses", "waits", "accepted_edges", "rtl_history"):
        if any(
            evidence[name]["inputs"].get(str(p.relative_to(ROOT)))
            != before[str(p.relative_to(ROOT))]
            for p in (core, wrapper)
        ):
            raise ValueError("CPU premise binds a different core: " + name)
    for name in ("dummy_responses", "waits", "accepted_edges"):
        if (evidence[name]["register_bits"], evidence[name]["excluded_register_bits"]) != (168, 0):
            raise ValueError("CPU premise has incomplete state inventory: " + name)
    idle = paths["waits"]
    if evidence["reset_release"]["inputs"].get(str(idle.relative_to(ROOT))) != digest(idle):
        raise ValueError("Reset release binds a different disabled-edge theorem")

    native = evidence["foundation"]
    initial = native["default_native_state"], native["default_native_pins"]
    source_prefix, source_contract = event_history.recipe(*initial)
    rtl_prefix, rtl_contract = cold_lda_event_history.recipe(*initial)
    vertices = [
        (event_history.DIRECTORY, source_prefix, source_contract),
        (cold_lda_event_history.DIRECTORY, rtl_prefix, rtl_contract),
    ]
    for directory, prefix, contract in vertices:
        if (directory / "prefix.v").read_text() != prefix or (
            directory / "contract.v"
        ).read_text() != contract:
            raise ValueError("Sealed instruction history differs from regenerated contract")
    if shared_property(source_contract) != shared_property(rtl_contract):
        raise ValueError("Original and RTL compare different bus or retirement observations")
    if len(evidence["source_history"]["opcodes"]) != 151:
        raise ValueError("Source instruction domain is incomplete")
    if not all(row["accepted"] for row in evidence["cold_source"]["checks"]):
        raise ValueError("Source LDA handoff was incomplete")
    macro_core, macro_wrapper = nmi_macro.transform(core.read_text(), wrapper.read_text())
    for name, text in (("macro-core.v", macro_core), ("macro-wrapper.v", macro_wrapper)):
        path = paths["accepted_edges"].with_name(name)
        if path.read_text() != text:
            raise ValueError("Accepted-edge theorem uses a different CPU macro")
    for path in (*paths.values(), *parent_paths):
        memory_composition.sealed(path)
    if before != hashes(inputs) or parents != current_machine.audit():
        raise ValueError("Cold CPU composition changed during verification")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes([directory / "contract.v" for directory, _, _ in vertices]),
            "premises": {name: str(path.relative_to(ROOT)) for name, path in paths.items()},
            "scope": "Cold Model B CPU startup through the first OS LDA and its documented-opcode continuation. Joins the exact source and RTL bus/retirement contracts, including arbitrary interrupt event summaries and physical waits after startup. The parent verdict supplies clock, read-only interrupt, original vector ROM and response timing premises at 32 and 96 MHz.",
            "limits": "One initial cold reset, quiet interrupts and no warm reset through first LDA. Warm restart in the continuation, proof that game fetches stay within the documented opcode domain, and common device response/interrupt generation are separate obligations.",
            "universal_game_equivalence": False,
        },
    )
    print("Cold CPU bus/retirement composition: PASS", flush=True)


if __name__ == "__main__":
    qualify()
