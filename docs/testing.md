# Testing

Run commands from the repository root after installing the
[toolchain](../README.md#getting-started). Keep generated builds, captures and
profiles under `build/`.

## Local suites

| Command                         | Selection                                                                                |
| ------------------------------- | ---------------------------------------------------------------------------------------- |
| `make validation-test-unit`     | Tooling tests                                                                            |
| `make validation-test-native`   | Seven native probe modules covering ADC, audio, disc, CPU reference, reset, pins and VIA |
| `make validation-test`          | Both partitions                                                                          |
| `make validation-test-extended` | Extra proof helpers and long native regressions                                          |
| `make bbc-c-proof-cache-test`   | Extended CPU proof and cache tests                                                       |

Before running the full suite, build the simulator and reference fixtures.

```sh
make bbc-build bbc-build96 bbc-capture-reference
make validation-build
make validation-test
```

`validation-build` enables the demo's capture-failure and PSG input tests.
Formal checks need their declared solvers. See
[reference bridges](bbc-reference-bridges.md#running-proofs) for setup and scope.

## Test selection and reports

```sh
make validation-test-unit VALIDATION_TEST_ARGS="--jobs 2"
make validation-test-native VALIDATION_TEST_ARGS="--pattern test_bbc_disc_oracle.py"
make validation-test-unit VALIDATION_TEST_ARGS="--batch-size 1"
```

The default worker count follows CPU affinity and container quota. Workers use
one compiler job each. Short modules share batches of up to eight by default.
Native probes run in their own processes. The extended keyboard-IRQ, loading
video and PSG sweep tests need one worker because they share generated models.

Each run writes `build/validation/tests/run-*/report.json` and per-module logs
and receipts. Reports include the selection, test outcomes, skipped checks,
worker failures and elapsed times. A worker crash preserves completed results
and fails any unfinished module. Prior timings change scheduling only.

## Demo models

`make test-fast` runs component tests and `make test-demo` runs frame scenarios.
Use `make demo-build` before running a Scala frame suite directly.
`SOC_FRAME_BACKEND=spinal` or `compare` selects the alternate frame backend.

`make verify` runs the internal integer models in `tools/oracle/`. Keep Q8.8
wrapping, signed division and firmware integer widths explicit. Register each
model's `check()` in `verify.MODULES`. Independent comparisons are listed in
[demo validation](elite-a-validation.md).

## RTL and gameplay

Select a component target from the [BBC runtime guide](bbc-runtime.md#test-loops)
or run `make bbc-test` for the broad regression. Check both master rates for
timing and pin changes. For gameplay, use the
[isolated replay](bbc-runtime.md#gameplay-replay) and its artifact checks.
Run sbt commands sequentially in each checkout because they share generated files.

Use `make clean-sim-cache` to drop SpinalSim objects, archives and precompiled
headers. Generated RTL, simulator libraries and captures remain available.
