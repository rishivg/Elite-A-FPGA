# BBC runtime

The BBC RTL runs the released Elite-A disc, OS, BASIC and DFS 1.2 on a Model B
without a Tube. The reference executes independently in jsbeeb or pinned C
models.

## Build

```sh
make bbc-build     # BbcSystemTop, 32 MHz
make bbc-build96   # BbcSystem96, 96 MHz with teletext
```

Preparation verifies downloads, derives the enabled Arlet CPU and builds the
simulators. Receipts bind source, ROMs, tools and generated files by content.
See [architecture](architecture.md) for board ports and clock enables.

## Test loops

Run these as `make <target>`. Use `make bbc-test` for the broad device regression
and [local validation suites](testing.md) for tooling and native probes.

| Area           | Targets                                                                |
| -------------- | ---------------------------------------------------------------------- |
| CPU            | `bbc-cpu-test`, `bbc-cpu-streams-test`                                 |
| Memory and bus | `bbc-bus-decode-test`, `bbc-ram-proof`, `bbc-rom-proof`                |
| VIA            | `bbc-via-test`, `bbc-via-serial-test`, `bbc-via-timer-history-test`    |
| ADC            | `bbc-adc-test`, `bbc-adc-sampling-test`, `bbc-adc-state-proof`         |
| Serial         | `bbc-serial-test`, `bbc-serial-cpu-test`, `bbc-serial-state-proof`     |
| Disc           | `bbc-disc-test`, `bbc-disc-command-test`, `bbc-disc-media-test`        |
| Video          | `bbc-video96-test`, `bbc-cursor96-test`, `bbc-cold-machine-video-test` |
| Audio          | `bbc-audio-test`, `bbc-psg-sweep-test`, `bbc-psg-state-proof`          |

## Validation status

The frozen 96 MHz build passed two independent executions of all 35 scheduled
scenarios. The comparison includes RAM, timed writes, video fields, digital
audio and persistence. The audit verified 277 frozen inputs, 6,720 capture
artifacts and 5,092 outcome assertions. Its summary was written to
`build/bbc/frozen-matrix-final-check.json`. The snapshot predates later CPU and
controller changes. [Replay counts](../README.md#gameplay-replay-results)

Full simulator equivalence across arbitrary CPU, peripheral and game histories
remains open, as do analogue I/O, board integration and routed timing. The
[reference bridges](bbc-reference-bridges.md#current-coverage-and-open-work) list
the proof obligations.

## Gameplay replay

| Command                  | Run                                               |
| ------------------------ | ------------------------------------------------- |
| `make bbc-validate96`    | Frozen 96 MHz strict matrix with timed bus writes |
| `make bbc-system96-test` | Four selected 96 MHz scenarios, twice each        |
| `make bbc-validate`      | 32 MHz replay with the broader equivalence gate   |

Each machine boots independently and receives the same input schedule.
`progress.json` records finished pairs. Open `build/bbc/latest/report.html`
for results, repeatability and provenance. An isolated bundle has its own
`execution.log` and `workspace/build/bbc/latest/report.html`.

Pass runner options in `BBC_VALIDATION_ARGS`. See
`python3 -m tools.bbc.run --help` for the full list.

- Use `--reuse-pairs-from <prior-run>` with matching inputs and capture settings.
  The runner checks both own-machine boots and each archived repetition, and
  reruns failed, missing or rejected captures.
- Use `--reuse-boots-from <prior-run>` to recover verified boots for a new
  selection or capture configuration. Both machines execute the new scenarios.

Supply the actual `build/bbc/runs/<run>` directory in the prior workspace.

## Digital restart

`warmReset` aborts pending FDC work and queues a CPU reset at the next
instruction boundary. Other peripherals continue ticking.

```sh
make bbc-cpu-warm-reset-test bbc-disc-restart-test
make bbc-machine-restart-test bbc-game-restart-test
```

The recorded CPU warm-reset corpus passed 579,712 cases at each of the three
clock periods and detected four deliberate RTL faults. The CPU report is
`build/bbc/cpu-warm-reset/result.json`.

## Working with long captures

Use `make bbc-isolated` to freeze models and runtime inputs during source
changes. `--jobs` bounds host captures. Lossless storage checks byte counts
and hashes when packing and reading artifacts. A recorded joystick capture
compressed 67,967,552 bytes of observations by 95.9% with identical decoded
bytes and comparison results.

`make bbc-profile` and `make bbc-profile96` qualify PGO builds against identical
capture hashes and bind the capture recipe. The strict 96 MHz target selects a verified profile when it
reduces CPU time on both qualification scenarios. Use `BBC_FULL_EVAL=1` to
evaluate falling edges as well when comparing the 96 MHz simulator paths.

## Rechecking existing captures

Seal the original contracts before adding assertions, then rerun
`make bbc-recheck`. For decoder repairs, seal both executions of each scenario
before editing, then rerun `make bbc-recompare` with the same run directory.
Set `run` to the completed run directory.

```sh
make bbc-recheck BBC_RECHECK_RUN="$run" BBC_RECHECK_ARGS=--seal-contracts
make bbc-recompare BBC_RECOMPARE_RUN="$run" BBC_RECOMPARE_ARGS=--seal
```

Recomparison writes its reports and images to `build/validation/bbc-recomparisons/`.
Use `--outcome-repairs <file>` in `BBC_RECOMPARE_ARGS` to record a correction
with its reason and exact before/after assertions. Changes to replay inputs,
reference code or RTL require fresh execution.
