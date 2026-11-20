# Build, validation and development commands for the BBC machine and demo.

SBT ?= sbt
PY  ?= python3
VERILATOR_JOBS ?= 4
BBC_VALIDATION_ARGS ?=
BBC_BOOT_BUNDLE ?=
BBC_RECHECK_RUN ?= $(CURDIR)/build/bbc/latest
BBC_RECHECK_ARGS ?=
BBC_RECOMPARE_RUN ?= $(CURDIR)/build/bbc/latest
BBC_RECOMPARE_ARGS ?=
BBC_PROOF_RECHECK_ARGS ?=
BBC_CBMC ?= $(or $(shell command -v cbmc 2>/dev/null),/usr/bin/cbmc)
BBC_C_SOURCE_JOBS ?= 2
VALIDATION_ARGS ?=
VALIDATION_TEST_ARGS ?=
export ELITE_FACE_CASES ?= $(CURDIR)/build/validation/render-latest/cases.tsv
export ELITE_FACE_FIRMWARE ?= $(CURDIR)/build/validation/render-latest/mmio.hex
export ELITE_MATRIX_CASES ?= $(CURDIR)/build/validation/render-latest/matrix.tsv
export ELITE_PIPELINE_CASES ?= $(CURDIR)/build/validation/render-latest/pipeline.tsv
export ELITE_ORIENTATION_CASES ?= $(CURDIR)/build/validation/render-latest/orientation.tsv
export ELITE_RAW_TRANSFORMS ?= $(CURDIR)/build/validation/render-latest/raw-transform.tsv
export ELITE_RAW_PIPELINES ?= $(CURDIR)/build/validation/render-latest/raw-pipeline.tsv
export ELITE_VERTEX_CASES ?= $(CURDIR)/build/validation/render-latest/vertex.tsv
export ELITE_PROJECTION_CASES ?= $(CURDIR)/build/validation/render-latest/projection.tsv
export ELITE_RAW_VERTICES ?= $(CURDIR)/build/validation/render-latest/raw-vertex.tsv

# RTL recipes share sbt's workspace and generated Verilog. Serialize these
# top-level targets even under make -j; native sub-builds retain explicit jobs.
.NOTPARALLEL:

.PHONY: help gen sim test ship raster render fw soc anim fly bubble combat tactics planet vista hyper market equip sysdata missile play play-smoke verify synth lint clean

help:
	@echo "BBC / released Elite-A:"
	@echo "  bbc-build / bbc-build96        Build the 32 / 96 MHz machine simulator"
	@echo "  bbc-test                      Independent CPU and BBC device regressions"
	@echo "  bbc-validate                  Strict repeated original-game comparison"
	@echo "  bbc-validate96                Full repeated scenario matrix at 96 MHz"
	@echo "  bbc-system96-test             Four whole-game scenarios twice at 96 MHz"
	@echo "  bbc-current-test              Current-source proofs and cold output comparison"
	@echo "  bbc-isolated                  Replay frozen copies while developing"
	@echo "  bbc-profile                   Train PGO and verify identical captures"
	@echo "  bbc-via-serial-test            Serial modes, access races and PB6/T2"
	@echo "  See docs/bbc-reference-bridges.md for CPU and device proof targets"
	@echo "  bbc-video96-test / bbc-cursor96-test  Independent 96 MHz pixel comparisons"
	@echo "  bbc-audio-test / bbc-video96-font-proof  Sound at both clocks / video preservation"
	@echo "  bbc-synth / bbc-synth96        ECP5 mapping at 32 / 96 MHz"
	@echo "  bbc-recheck / bbc-recompare    Recheck sealed observations; see docs/bbc-runtime.md"
	@echo "Development:"
	@echo "  validation-test[-unit/-native] Python/native suites with per-module reports"
	@echo "  validation-test-extended      Extended proof and long native regressions (serial)"
	@echo "  clean                         Remove generated build artifacts"
	@echo "Simplified PicoRV32 demo:"
	@echo "  fw gen lint                   Build firmware and Verilog; lint RTL"
	@echo "  test / test-fast / test-demo   All / component / demo-frame RTL suites"
	@echo "  play / play-smoke              Interactive SDL2 / headless game"
	@echo "  validation-render             Original rendering kernels and CPU MMIO"
	@echo "  validate-elite-a               Compare the simplified demo with Elite-A"
	@echo "Usage: make <target>; see README.md and docs/testing.md"

.DEFAULT_GOAL := help

include make/demo.mk
include make/validation.mk
include make/bbc.mk

.PHONY: clean-sim-cache
# Drop compiler intermediates; keep generated RTL, simulator libraries and logs.
clean-sim-cache:
	@if [ -d simWorkspace ]; then find simWorkspace -type f \( -name '*.o' -o -name '*.a' -o -name '*.gch' \) -delete; fi

clean:
	rm -rf build target project/target project/project simWorkspace obj_dir
