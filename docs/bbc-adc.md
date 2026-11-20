# BBC uPD7002 ADC

The ADC retains the previous result during conversion. An accepted high-byte
read acknowledges EOC, both low-byte addresses alias, and system VIA CB1 sees
completion and acknowledgement at the accepted bus edge. PHI2 stays high
throughout the first 2 MHz slot and low throughout the second.

| Target                  | Coverage                                                                                                                                                                                       |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bbc-adc-test`          | 256 NEC control pairs with 4,352 bus observations, 112 ADC/VIA cases with 2,000 observations, and all 1,048,576 completed-result cases against unchanged beebjit C.                            |
| `bbc-adc96-test`        | The same 112 bus-sequence cases and 2,000 observations on every 96 MHz master edge, using the generated ADC and VIA modules.                                                                   |
| `bbc-adc-sampling-test` | 320 cases and 7,280 observations per rate for input changes, both durations and restart near completion. A start-time sampling fault must fail.                                                |
| `bbc-adc-state-proof`   | Induction over arbitrary digital transactions after fixture reset. It checks retained state, deadlines, readback, EOC and collisions, with separate wrong-duration and wrong-channel controls. |

Results and logs live under `build/bbc/adc-*/`. The state proof binds the
actual parent clocks, reset and input connections at both master rates.

The default `bbc-result-bits` reference profile combines the NEC bus
semantics, C-checked result bits and nominal 4 ms and 10 ms scheduling. It
seals the pending command in a saved conversion. Use `--adc-profile original`
to reproduce the unmodified JavaScript behavior.

NEC specifies voltage-dependent conversion duration, lacks a power-on reset
and allows uncertain readback or EOC during concurrent transfer. Deterministic
startup, fixed duration and completion-time sampling are digital model choices.
`readRace` exposes the concurrent-transfer window. Physical timing, sampling
aperture and analogue accuracy need independent measurements.
[NEC datasheet, pages 8.78 and 8.81–8.84](https://www.farnell.com/datasheets/100687.pdf)
