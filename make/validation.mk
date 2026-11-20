# Shared test runner and original renderer/demo comparisons.

.PHONY: validation-reference validation-build validate-elite-a validation-test validation-video validation-audio validation-render validation-components validation
validation-reference:
	$(PY) -m tools.validation.setup

validation-build: fw gen
	cd build/rtl && verilator --cc --exe --build -j $(VERILATOR_JOBS) -Wno-fatal --top-module Soc \
	  -CFLAGS "-O2 -std=c++17" -o validate --Mdir obj_validate \
	  Soc.v ../../third_party/picorv32.v ../../sim/native/validate.cpp
	cd build/rtl && verilator --cc --exe --build -j $(VERILATOR_JOBS) -Wno-fatal --top-module Psg \
	  -CFLAGS "-O2 -std=c++17" -o validate_psg --Mdir obj_validate_psg \
	  Psg.v ../../sim/native/validate_psg.cpp

validate-elite-a: validation-build
	$(MAKE) validation-render
	$(PY) -m tools.validation.run $(VALIDATION_ARGS)

validation-test:
	$(PY) -m tools.validation.test $(VALIDATION_TEST_ARGS)

.PHONY: validation-test-unit validation-test-native validation-test-extended bbc-c-boundary-recheck bbc-c-proof-cache-test
validation-test-unit:
	$(PY) -m tools.validation.test --suite unit $(VALIDATION_TEST_ARGS)

validation-test-native:
	$(PY) -m tools.validation.test --suite native $(VALIDATION_TEST_ARGS)

validation-test-extended:
	$(PY) -m tools.validation.test --suite extended $(VALIDATION_TEST_ARGS)

bbc-c-boundary-recheck:
	$(PY) -m tools.bbc.proofs.cpu_reference.recheck_boundaries $(BBC_PROOF_RECHECK_ARGS)

bbc-c-proof-cache-test:
	$(PY) -m tools.validation.test --suite extended --pattern 'test_c_*.py' $(VALIDATION_TEST_ARGS)

validation-components: fw
	$(SBT) 'testOnly leet.sim.FramePresenterSim leet.sim.BoardInputsSim leet.sim.InputCaptureSim leet.sim.VgaTimingSim leet.sim.ScaledScanoutSim leet.sim.PsgSim leet.sim.RasterBoundsSim leet.sim.RendererBoundsSim leet.sim.ProjectSim leet.sim.FirmwareImageTest'

validation-audio: validation-build
	$(PY) -m tools.validation.psg

validation-render:
	$(PY) -m tools.validation.render

# Ordered recipe keeps sbt/elaboration and native generated sources from racing
# even with make -j. The final comparison retains failures and coverage gaps.
validation:
	$(MAKE) validation-build
	$(MAKE) validation-test validation-components
	$(MAKE) lint
	$(MAKE) validation-video
	$(MAKE) validation-render
	$(PY) -m tools.validation.run $(VALIDATION_ARGS)

validation-video: fw gen
	cd build/rtl && verilator --cc --exe --build -j $(VERILATOR_JOBS) -Wno-fatal --top-module EliteTop \
	  -CFLAGS "-O2 -std=c++17" -o validate_video --Mdir obj_validate_video \
	  EliteTop.v ../../third_party/picorv32.v ../../sim/native/validate_video.cpp
	$(PY) -m tools.validation.video
