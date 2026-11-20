"""Bind the proved video and device endpoints to the shared reference epoch."""

from pathlib import Path

from tools.bbc import build, video96
from tools.bbc.proofs import (
    adc_scheduler,
    current_machine,
    disc_nmi_order,
    memory_composition,
    reference_bus_epoch,
    video_parent_phase,
    video_source_clock,
)
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json

DIRECTORY = build.BBC / "reference-timing-composition"


def qualify(*, refresh_adc=False):
    generation = current_machine.audit()
    if refresh_adc:
        adc_scheduler.qualify(
            parents=current_machine.parents(), directory=DIRECTORY / "adc-deadline"
        )
    current = {
        "bus": reference_bus_epoch.DIRECTORY,
        "video_phase": video_parent_phase.DIRECTORY,
        "video_source_clock": video_source_clock.DIRECTORY,
        "disc_ack": disc_nmi_order.DIRECTORY,
        "adc_deadline": DIRECTORY / "adc-deadline",
    }
    wired = {
        "peripheral_pins": build.BBC / "peripheral-pin-binding-proof",
        "via_irq_forwarding": build.BBC / "via-interrupt-state-contract",
        "sound_bus": build.BBC / "sound-bus-state-proof",
        "digital_psg": build.BBC / "psg-state-proof",
    }
    paths = {name: path / "result.json" for name, path in (current | wired).items()}
    production = [build.BBC / "BbcMachine.v", video96.DIRECTORY / "BbcMachine96.v"]
    private = [path for _, path, _ in current_machine.parents()]
    files = [
        Path(__file__),
        Path(memory_composition.__file__),
        Path(current_machine.__file__),
        *paths.values(),
        *private,
        *production,
    ]
    before = hashes(files)
    for capture, elaborate in zip(production, private, strict=True):
        if digest(capture) != digest(elaborate):
            raise ValueError("Capture and proof use different generated parents")
    for name, receipt in paths.items():
        result = memory_composition.sealed(receipt)
        for parent in private if name in current else production:
            inputs = result["inputs"]
            if inputs.get(str(parent.relative_to(ROOT)), inputs.get(str(parent))) != digest(parent):
                raise ValueError("Reference timing premise uses a different parent: " + name)
    for receipt in paths.values():
        memory_composition.sealed(receipt)
    if before != hashes(files) or generation != current_machine.audit():
        raise ValueError("Reference timing evidence changed")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes([reference_bus_epoch.DIRECTORY / "original-slots.bin"]),
            "premises": {name: str(path.relative_to(ROOT)) for name, path in paths.items()},
            "scope": "Shared power-on Model B CPU bus epoch, video character phase, VIA/ADC, serial and sound endpoints for both master rates. Binds actual VIA video/ADC/sound routing, IRQ/readback forwarding under its event-mask contract, digital PSG and sound deadline contracts, and the independent first-byte FDC acknowledgement corpus. Actual replay parents are byte-identical to the qualified generated parents.",
            "limits": "Device transition and observable output equality for arbitrary histories, complete monitor/teletext rendering and universal whole-machine equivalence are separate from these endpoint bindings.",
            "universal_game_equivalence": False,
        },
    )
    print("Shared reference timing bindings: PASS", flush=True)


if __name__ == "__main__":
    qualify(refresh_adc=True)
