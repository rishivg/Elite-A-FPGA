# Independent reference bridges

## Current coverage and open work

The source bridges compare the production RTL with the pinned C CPU model and
independently executed jsbeeb device code. Each result has its own initialization,
inputs, observation boundary and proof domain.

| Area              | Qualified properties and limits                                                                                                                                                                                                                                           |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CPU               | All 151 documented instructions compose in an initialized stream with IRQ, NMI and warm reset inactive. Separate contracts cover accepted-edge, wait, interrupt and reset histories under their declared entry, opcode and pin assumptions.                               |
| Memory and timing | Both machine clocks have checks for CPU request spacing, RAM/ROM selection and response settlement, ADC scheduling and video fetch timing. These serve as premises for CPU and peripheral composition.                                                                    |
| Display           | Local CRTC histories and bitmap/teletext pipelines have source or preservation checks. Nonzero HSYNC widths and blank zero-width startup have separate proofs. Unrestricted zero-width histories have a known divergence.                                                 |
| FDC               | Source projections cover specific register, command, timer and FM callback boundaries. Pending-byte IRQ checks use the actual 934-bit controller at both rates and cover gap, write-run and C7/FB marker callbacks under their declared framing and ordering assumptions. |
| Startup           | Separate current-source elaborations combine cold and warm CPU startup, CPU event binding, parent clock/memory/pin checks and local display histories. A cold replay exercises the combined implementation for a finite prefix.                                           |

Arbitrary device/CPU composition, unrestricted READ framing, CRC-tail and
queued-callback interactions, formatting and weak cells remain open. The complete
ROM opcode and reset history, video modes, analogue behavior and physical FPGA
timing require their own evidence. See the [runtime status](bbc-runtime.md#validation-status)
for the frozen gameplay result.

## Running proofs

Use Yosys for SAT and the solver required by the selected proof. CBMC is required
for the durable C foundation. Install the pinned JavaScript parser for source
extraction with `npm ci --ignore-scripts --no-audit --no-fund`.

### CPU and startup

| Target                                                                | Scope                                                                                                                                                                                                                      |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bbc-cpu-documented-induction-proof`                                  | Actual RTL vs. documented NMOS semantics. Induction and actual RTL mutations use the full CPU. The reduced graph checks all 22,801 admissible first/next opcode pairs and 210 excluded positions.                          |
| `bbc-c-source-foundation`                                             | Original C construction and 100,000 native transitions, then a fresh SAT check of the complete 320-input/321-output transition. The 65 partitions cover read holds, 1,115 defined microsteps and all 256 incoming opcodes. |
| `bbc-c-source-profile-proof`, `bbc-source-history-proof`              | Source initialization/profile and arbitrary-length instruction histories within the sealed domain.                                                                                                                         |
| `bbc-source-history-faults`, `bbc-source-reset-history-handoff`       | Fresh mutation queries and the connection from source reset to admissible instruction history.                                                                                                                             |
| `bbc-source-nmi-sampling-proof`, `bbc-source-physical-mixed-rtl-test` | Native event capture on physical edges, including waits, plus selected-instruction histories with arbitrary IRQ/NMI inputs.                                                                                                |
| `bbc-source-macro-mixed-rtl-test`, `bbc-source-macro-warm-rtl-test`   | Accepted-edge compositions and warm requests under the declared physical transition relation.                                                                                                                              |
| `bbc-current-test`                                                    | Current-source CPU, memory, peripheral and display bindings plus a cold replay. `bbc-current-gen` generates separate netlists.                                                                                             |

Run a target with `make <target>`. The current-source outputs are under
`build/bbc/equivalence-current/`. Each producer prints or records its result path.

### CPU event and device composition

Regenerate the cold event premises with `bbc-source-event-history-proof`,
`bbc-source-rtl-event-history-proof`, `bbc-source-lda-handoff-proof` and
`bbc-source-cold-event-history-proof` in that order after qualifying the
foundation, source history, mixed CPU and NMI/macro-transition proofs. The
`bbc-source-event-composition`, `bbc-source-parent-schedule` and
`bbc-source-response-settlement` targets consume the sealed results. Request
spacing and timing do not supply the response bytes or device event values.

| Target                                                                                                | Scope                                                                                                                                                                                  |
| ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bbc-source-event-capture`, `bbc-source-device-events`, `bbc-source-cpu-explicit-events`              | Original callback transitions, finite RTL abort/reset/retry windows and actual CPU event-latch bindings.                                                                               |
| `bbc-source-callback-summary`, `bbc-source-callback-cpu`                                              | Callback/acknowledgement order, source ticks with zero-time dispatch and shared-reset epochs. CPU composition requires matching response bytes, sampled IRQs and retained NMI windows. |
| `bbc-source-fdc-reset`, `bbc-source-fdc-commands`, `bbc-source-fdc-timers`                            | Reset cancellation, partial-command ownership and timer preservation/cancellation.                                                                                                     |
| `bbc-source-fdc-next-five`, `bbc-source-fdc-shared`, `bbc-source-fdc-order`                           | Live register aliases, SPECIFY, step/retry and timer/index priority.                                                                                                                   |
| `bbc-source-fdc-bits`, `bbc-source-fdc-sync`, `bbc-source-fdc-concurrent`                             | CRC/bit kernels and qualified delimiter and callback overlaps.                                                                                                                         |
| `bbc-source-fdc-byte`, `bbc-source-fdc-crc`, `bbc-source-fdc-marker`                                  | Byte dispatch, ID CRC, C7/FB markers and result writes.                                                                                                                                |
| `bbc-source-fdc-completion`, `bbc-source-fdc-ready`, `bbc-source-fdc-gap`, `bbc-source-fdc-write-gap` | Sector completion, READY readback, live R21 gap counters and repeated write-run data.                                                                                                  |
| `bbc-source-fdc-pending-irq`                                                                          | Pending serial-byte IRQ before the byte body. The recorded qualification used 33 controls across both rates and 60 cold histories per rate, with 2,340 observations per rate.          |

FDC proofs assert per-property APIs, loaded drive, media alignment and callback
ordering. Check the report's scope before composing results. The storage
interface must settle after the base edge and hold through intervening master
samples.

### Video and parent time

`bbc-reference-timing-composition` composes the source bus epoch, actual video
parent phase, disc NMI order and VIA, sound and pin contracts. Use
`bbc-hsync-source-history-proof` for the HSYNC properties and
`bbc-hsync-observability-proof` for the production connections. See the
[video guide](bbc-video.md) for bitmap, cursor and teletext tests.

## Reusing proof preparation

Proof receipts seal their inputs, generated models and logs. Composition rejects
missing or changed evidence. Prepared graphs can be reused when their inputs
match. Solver queries and DUT executions still run afresh where requested.
Scoped negative controls must produce the expected counterexample. Run
`make bbc-c-proof-cache-test` for the helper and cache regressions.
