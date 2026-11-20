"""Regenerate the complete official-opcode source history contract from sources."""

from pathlib import Path

from tools.bbc.proofs.cpu_reference import (
    branch_interrupts,
    cold_entry,
    control_induction,
    documented_model,
    eligible_stream,
    fetch_seed,
    instructions,
    interrupt_stream,
    memory_interrupts,
    protocol_invariants,
    short_irq,
    short_stream,
    zero_page_interrupts,
)
from tools.bbc.proofs.video_cursor import replace_once
from tools.validation.common import ROOT


def producer_files():
    """Files implementing this recipe and the helpers it calls transitively."""
    return [
        Path(__file__),
        *[
            Path(module.__file__)
            for module in (
                control_induction,
                branch_interrupts,
                cold_entry,
                documented_model,
                eligible_stream,
                fetch_seed,
                interrupt_stream,
                instructions,
                memory_interrupts,
                protocol_invariants,
                short_irq,
                short_stream,
                zero_page_interrupts,
            )
        ],
    ]


def generate(initial, pins):
    prefix = interrupt_stream.prefix(
        initial, pins, (ROOT / "sim/formal/BbcCpuStreamPrefix.v").read_text()
    )
    prefix = replace_once(
        prefix,
        "output [255:0] proofState, output [63:0] proofPins, output proofTrap",
        "output [63:0] proofResponse, output [255:0] proofState, output [63:0] proofPins, output proofTrap, output [255:0] proofHistory, output proofPreviousFetch",
    )
    prefix = replace_once(
        prefix,
        "assign proofState=referenceState;",
        "assign proofResponse=response;\nassign proofState=referenceState;",
    )
    prefix = replace_once(
        prefix,
        "endmodule",
        "assign proofHistory=retirementState;assign proofPreviousFetch=previousFetch;\nendmodule",
    )
    contract = memory_interrupts.strengthen(
        (ROOT / "sim/formal/BbcCpuInterruptStreamContract.v").read_text()
    )
    contract = replace_once(
        contract,
        "wire [255:0] cState,cHistory;",
        documented_model.context_function(documented_model.inventory())
        + "wire [63:0] cResponse;\nwire [255:0] cState,cHistory;",
    )
    contract = replace_once(
        contract, ".proofState(cState)", ".proofResponse(cResponse),.proofState(cState)"
    )
    contract = replace_once(
        contract,
        "(* keep *) wire stateRelation=",
        """(* keep *) wire sourceContext=(cResponse[24] && cResponse[28]) ||
 (cResponse[25] ? documented(cResponse[23:16]) :
 (cState[255:251]==0 && documented(cState[250:243])));
(* keep *) wire stateRelation=sourceContext &&
""",
    )
    return fetch_seed.prefix(prefix), fetch_seed.contract(control_induction.strengthen(contract))
