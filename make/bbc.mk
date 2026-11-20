# BBC builds, device regressions, gameplay replay and formal checks.

.PHONY: bbc-prepare bbc-gen bbc-build bbc-build96 bbc-system96-test bbc-cursor96-test bbc-profile bbc-cpu-test bbc-reference-test bbc-audio-test bbc-via-test bbc-via-serial-test bbc-via-proof bbc-adc-test bbc-system-test bbc-crtc-test bbc-lightpen-test bbc-video-test bbc-cursor-test bbc-cursor-cold-test bbc-video-proof bbc-teletext-test bbc-teletext-proof bbc-video96-font-proof bbc-isolated bbc-disc-test bbc-disc-proof bbc-rtl-check bbc-test bbc-validate bbc-recheck bbc-recompare bbc-synth bbc-synth96
bbc-prepare:
	$(PY) -m tools.validation.setup
	$(PY) -m tools.bbc.prepare
	$(PY) -m tools.bbc.cpu_events

bbc-gen: bbc-prepare
	$(PY) -m tools.bbc.build --generate-only

bbc-build: bbc-prepare
	$(PY) -m tools.bbc.build

# Execute every master edge of the 96 MHz machine, including whole-game I/O.
bbc-build96: bbc-gen
	$(PY) -m tools.bbc.system96

bbc-system96-test: bbc-build96
	$(PY) -m tools.bbc.run --clock-multiplier 3 --scenario initial-status --scenario equipment --scenario flight-combat --scenario joystick-arbitrary-bits-fire --repeat 2 --jobs 2

bbc-profile: bbc-build
	$(PY) -m tools.bbc.profile

bbc-reference-test: bbc-prepare
	$(PY) -m tools.bbc.prepare --cpu-fixtures
	$(PY) -m tools.bbc.cycle_reference

bbc-audio-test: bbc-gen
	$(PY) -m tools.bbc.audio_probe

.PHONY: bbc-audio-proof
bbc-audio-proof: bbc-gen
	$(PY) -m tools.bbc.audio_proof

.PHONY: bbc-psg-sweep-test
bbc-psg-sweep-test: bbc-gen
	$(PY) -m unittest discover -s tests/bbc_extended -p test_psg_sweep.py

.PHONY: bbc-reset-test bbc-key-irq-test bbc-boot-trace bbc-loading-video-test bbc-machine-clock-proof bbc-capture-reference
bbc-capture-reference: bbc-gen bbc-reference-test
	$(PY) -m tools.bbc.adc_results

bbc-reset-test: bbc-build bbc-build96 bbc-capture-reference
	$(PY) -m unittest discover -s tests/validation -p test_bbc_reset_prefix.py

bbc-key-irq-test: bbc-build bbc-build96 bbc-capture-reference
	$(PY) -m unittest discover -s tests/bbc_extended -p test_key_irq.py

bbc-boot-trace: bbc-build bbc-capture-reference
	$(PY) -m tools.bbc.boot_trace

bbc-loading-video-test: bbc-capture-reference
	$(PY) -m tools.bbc.loading_video

.PHONY: bbc-cold-machine-video-test
bbc-cold-machine-video-test: bbc-build96 bbc-capture-reference
	$(PY) -m tools.bbc.cold_machine_video $(BBC_COLD_VIDEO_ARGS)

bbc-machine-clock-proof: bbc-gen
	$(PY) -m tools.bbc.machine_clock_proof

.PHONY: bbc-disc-clocked-boundaries-test
bbc-disc-clocked-boundaries-test: bbc-machine-clock-proof
	$(PY) -m tools.bbc.proofs.disc_clocking

.PHONY: bbc-bus-decode-test bbc-bus-binding-proof bbc-serial-test bbc-serial-state-proof bbc-serial-cpu-test
bbc-bus-decode-test: bbc-gen
	$(PY) -m tools.bbc.bus_decode

bbc-bus-binding-proof: bbc-gen
	$(PY) -m tools.bbc.machine_clock_proof
	$(PY) -m tools.bbc.bus_binding_proof

bbc-serial-test: bbc-bus-binding-proof
	$(PY) -m tools.bbc.serial_probe

bbc-serial-state-proof: bbc-serial-test
	$(PY) -m tools.bbc.serial_state_proof

bbc-serial-cpu-test: bbc-build bbc-build96 bbc-reference-test
	$(PY) -m tools.bbc.serial_cpu_probe

.PHONY: bbc-scroll-proof
bbc-scroll-proof: bbc-gen
	$(PY) -m tools.bbc.scroll_proof

.PHONY: bbc-ram-proof bbc-memory-binding-proof bbc-rom-proof
bbc-ram-proof: bbc-gen
	$(PY) -m tools.bbc.ram_proof

bbc-memory-binding-proof: bbc-gen
	$(PY) -m tools.bbc.memory_binding_proof

bbc-rom-proof: bbc-gen
	$(PY) -m tools.bbc.rom_proof

.PHONY: bbc-psg-state-proof bbc-sound-bus-state-proof
bbc-psg-state-proof: bbc-gen bbc-psg-sweep-test
	$(PY) -m tools.bbc.psg_state_proof

bbc-sound-bus-state-proof: bbc-gen
	$(PY) -c 'from tools.bbc.audio_probe import qualify_bus; qualify_bus(); qualify_bus(clock_multiplier=3)'
	$(PY) -m tools.bbc.sound_bus_state_proof

.PHONY: bbc-via-cpu-endpoint-test bbc-profile96
.PHONY: bbc-adc-sampling-test
bbc-adc-sampling-test: bbc-gen
	$(PY) -m tools.bbc.adc_sampling

.PHONY: bbc-adc-state-proof
bbc-adc-state-proof: bbc-gen
	$(PY) -m tools.bbc.machine_clock_proof
	$(PY) -m tools.bbc.adc_results
	$(PY) -m tools.bbc.adc_sampling
	$(PY) -m tools.bbc.adc_state_proof

bbc-via-cpu-endpoint-test: bbc-gen
	$(PY) -m tools.bbc.via_cpu_endpoint
	$(PY) -m tools.bbc.via_cpu_endpoint --clock-multiplier 3

bbc-profile96: bbc-build96
	$(PY) -m tools.bbc.profile96

.PHONY: bbc-crtc-transition-test
bbc-crtc-transition-test: bbc-gen
	$(PY) -c 'from tools.bbc.crtc_probe import qualify; qualify(dense=True, transitions=True)'

.PHONY: bbc-video-transition-test
bbc-video-transition-test: bbc-gen
	$(PY) -c 'from tools.bbc.video_probe import qualify; qualify(mode_transitions=True); qualify(clock_transitions=True)'

.PHONY: bbc-adc96-test
bbc-adc96-test: bbc-gen
	$(PY) -c 'from tools.bbc.adc_probe import qualify; qualify(clock_multiplier=3)'

.PHONY: bbc-cpu-arithmetic-test bbc-cpu-decimal-transitions-test bbc-cpu-brk-test bbc-cpu-sequences-test bbc-cpu-streams-test bbc-cpu-stack-streams-test bbc-cpu-instruction-proof
bbc-cpu-arithmetic-test: bbc-prepare
	$(PY) -m tools.bbc.cpu_arithmetic

bbc-cpu-decimal-transitions-test: bbc-prepare
	$(PY) -m tools.bbc.cpu_arithmetic --transitions

bbc-cpu-brk-test: bbc-prepare
	$(PY) -m tools.bbc.proofs.cpu_build_cache cpu-brk
	$(PY) -m tools.bbc.cpu_brk

bbc-cpu-instruction-proof: bbc-prepare
	$(PY) -m tools.bbc.proofs.cpu_build_cache cpu-brk cpu-sequences
	$(PY) -m tools.bbc.cpu_instruction_check

.PHONY: bbc-cpu-immediate-pair-proof
bbc-cpu-immediate-pair-proof: bbc-gen
	$(PY) -m tools.bbc.proofs.immediate_pairs

.PHONY: bbc-cpu-immediate-induction-proof
bbc-cpu-immediate-induction-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-immediate-induction-proof

.PHONY: bbc-cpu-two-cycle-induction-proof
bbc-cpu-two-cycle-induction-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-two-cycle-induction-proof

.PHONY: bbc-cpu-branch-induction-proof
bbc-cpu-branch-induction-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-branch-induction-proof

.PHONY: bbc-cpu-direct-memory-induction-proof
bbc-cpu-direct-memory-induction-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-direct-memory-induction-proof

.PHONY: bbc-cpu-memory-induction-proof
bbc-cpu-memory-induction-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-memory-induction-proof

bbc-cpu-sequences-test: bbc-prepare
	$(PY) -m tools.bbc.proofs.cpu_build_cache cpu-sequences
	$(PY) -m tools.bbc.cpu_sequences

bbc-cpu-streams-test: bbc-prepare
	$(PY) -m tools.bbc.proofs.cpu_build_cache cpu-streams
	$(PY) -m tools.bbc.cpu_streams

bbc-cpu-stack-streams-test: bbc-prepare
	$(PY) -m tools.bbc.proofs.cpu_build_cache cpu-stack-streams
	$(PY) -m tools.bbc.cpu_streams --stack

.PHONY: bbc-cpu-values-test bbc-cpu-logic-test bbc-cpu-idle-proof bbc-video96-transition-test
bbc-cpu-values-test: bbc-prepare
	$(PY) -m tools.bbc.cpu_values

bbc-cpu-logic-test: bbc-prepare
	$(PY) -c 'from tools.bbc.cpu_arithmetic import qualify; qualify(logic=True)'

bbc-cpu-idle-proof: bbc-prepare
	$(PY) -m tools.bbc.cpu_idle_proof

bbc-video96-transition-test: bbc-gen
	$(PY) -c 'from tools.bbc.video_probe import qualify; qualify(clock_transitions=True, clock_multiplier=3); qualify(mode_transitions=True, clock_multiplier=3); qualify(clock_multiplier=3)'

bbc-via-test: bbc-gen
	$(PY) -m tools.bbc.via_probe
	$(PY) -m tools.bbc.via_timer_history
	$(PY) -m tools.bbc.via_pulse_load
	$(PY) -m tools.bbc.via_control
	$(PY) -m tools.bbc.via_t2_shift
	$(PY) -m tools.bbc.via_serial
	$(PY) -m tools.bbc.via_serial_corners

.PHONY: bbc-via-timer-history-test
bbc-via-timer-history-test: bbc-gen
	$(PY) -m tools.bbc.via_timer_history

.PHONY: bbc-via-timer1-state-proof
bbc-via-timer1-state-proof: bbc-via-timer-history-test
	$(PY) -m tools.bbc.via_timer1_state_proof

.PHONY: bbc-via-pulse-load-test bbc-via-timer2-state-proof
bbc-via-pulse-load-test: bbc-gen
	$(PY) -m tools.bbc.via_pulse_load

bbc-via-timer2-state-proof: bbc-via-pulse-load-test
	$(PY) -m tools.bbc.via_timer2_state_proof

.PHONY: bbc-cpu-interrupt-matrix-test
bbc-cpu-interrupt-matrix-test: bbc-gen
	$(PY) -m tools.bbc.cpu_interrupt_matrix

.PHONY: bbc-cpu-warm-reset-test
bbc-cpu-warm-reset-test: bbc-prepare
	$(PY) -m tools.bbc.cpu_warm_reset

.PHONY: bbc-cpu-cold-preservation bbc-disc-restart-test bbc-machine-restart-test bbc-game-restart-test
bbc-cpu-cold-preservation: bbc-prepare
	$(PY) -m tools.bbc.cpu_cold_preservation

bbc-disc-restart-test: bbc-gen
	$(PY) -m tools.bbc.disc_restart

.PHONY: bbc-disc-alias-test bbc-disc-recovery-test bbc-disc-bounds-test bbc-disc-seek-test bbc-disc-command-test bbc-disc-counter-phases-test bbc-reference-media-path-test
bbc-disc-alias-test: bbc-gen
	$(PY) -m tools.bbc.disc_aliases

bbc-disc-recovery-test: bbc-gen
	$(PY) -m tools.bbc.disc_recovery

bbc-disc-bounds-test: bbc-gen
	$(PY) -m tools.bbc.disc_bounds

bbc-disc-seek-test: bbc-gen
	$(PY) -m tools.bbc.disc_seek

bbc-disc-command-test: bbc-gen
	$(PY) -m tools.bbc.disc_commands

bbc-disc-counter-phases-test: bbc-gen
	$(PY) -m tools.bbc.disc_counter_phases

.PHONY: bbc-disc-register-test
bbc-disc-register-test: bbc-gen
	$(PY) -m tools.bbc.disc_registers

.PHONY: bbc-disc-media-test bbc-disc-active-test
bbc-disc-media-test: bbc-gen
	$(PY) -m tools.bbc.disc_media

bbc-disc-active-test: bbc-gen
	$(PY) -m tools.bbc.disc_active

bbc-reference-media-path-test: bbc-prepare
	$(PY) -c 'from tools.validation.common import source_root; from tools.bbc.build import BBC; import subprocess; subprocess.run(["node", "tools/bbc/reference_media_paths.mjs", str(source_root("jsbeeb")), str(BBC / "disc.ssd")], check=True)'

bbc-machine-restart-test: bbc-build bbc-build96 bbc-reference-test
	$(PY) -m tools.bbc.machine_restart

bbc-game-restart-test: bbc-build bbc-build96 bbc-reference-test
	$(PY) -m tools.bbc.machine_restart --profile game

.PHONY: bbc-cpu-interrupt-proof
bbc-cpu-interrupt-proof: bbc-cpu-interrupt-matrix-test
	$(PY) -m tools.bbc.cpu_interrupt_proof

bbc-via-serial-test: bbc-gen
	$(PY) -m tools.bbc.via_serial
	$(PY) -m tools.bbc.via_serial_corners

.PHONY: bbc-via-serial-dense-test
bbc-via-serial-dense-test: bbc-gen
	$(PY) -c 'from tools.bbc import via_serial, via_serial_corners; [(via_serial.qualify(clock_multiplier=p), via_serial_corners.qualify(clock_multiplier=p)) for p in (1, 3)]'

bbc-via-proof: bbc-gen
	$(PY) -m tools.bbc.via_proof

bbc-adc-test: bbc-gen
	$(PY) -m tools.bbc.adc_probe
	$(PY) -m tools.bbc.adc_bus
	$(PY) -m tools.bbc.adc_results

bbc-system-test: bbc-gen
	$(PY) -m tools.bbc.system_probe

bbc-crtc-test: bbc-gen
	$(PY) -m tools.bbc.crtc_probe
	$(PY) -m tools.bbc.crtc_registers

bbc-lightpen-test: bbc-gen
	$(PY) -m tools.bbc.lightpen

bbc-video-proof: bbc-gen
	$(PY) -m tools.bbc.video_proof

bbc-video-test: bbc-gen
	$(PY) -m tools.bbc.video_probe
	$(PY) -m tools.bbc.interlace

.PHONY: bbc-video96-test bbc-video96-reference
bbc-video96-test: bbc-gen
	$(PY) -m tools.bbc.video96

# Diagnostic: the unchanged JavaScript model documents blanking/reset limits.
# Retain its complete counterexample rather than declaring a matching C run universal.
bbc-video96-reference: bbc-gen
	$(PY) -c 'from tools.bbc.interlace import qualify; qualify(clock_multiplier=3, teletext=True)'

bbc-cursor-test: bbc-gen
	$(PY) -m tools.bbc.cursor

bbc-cursor96-test: bbc-gen
	$(PY) -m tools.bbc.cursor --clock-multiplier 3

bbc-cursor-cold-test: bbc-gen
	$(PY) -m tools.bbc.cursor --cold

.PHONY: bbc-teletext-cursor-test bbc-teletext-cursor-cold-test bbc-teletext-cursor-corners-test bbc-boot-audit
bbc-teletext-cursor-test: bbc-gen
	$(PY) -c 'from tools.bbc.teletext_video import qualify; qualify(cursor=True)'

bbc-teletext-cursor-cold-test: bbc-gen
	$(PY) -c 'from tools.bbc.teletext_video import qualify; qualify(cursor=True, cold=True)'

bbc-teletext-cursor-corners-test: bbc-gen
	$(PY) -c 'from tools.bbc.teletext_video import qualify; qualify(cursor=True, corners=True)'

# Supply the run directory inside a frozen isolated execution bundle.
bbc-boot-audit:
	@test -n "$(BBC_AUDIT_RUN)" || { echo 'Set BBC_AUDIT_RUN to a frozen run directory'; exit 2; }
	$(PY) -m tools.bbc.boot_audit "$(BBC_AUDIT_RUN)"

bbc-teletext-test:
	$(PY) -m tools.bbc.teletext

bbc-teletext-proof:
	$(PY) -m tools.bbc.teletext_proof

bbc-video96-font-proof:
	$(PY) -m tools.bbc.video96_font_proof

bbc-disc-test: bbc-gen
	$(PY) -m tools.bbc.disc_probe

bbc-disc-proof:
	yosys -p 'read_verilog sim/formal/BbcDiscArithmetic.v; prep -top disc_arithmetic; sat -prove equal 1 -verify'

bbc-cpu-test: bbc-prepare
	$(PY) -m tools.bbc.prepare --cpu-fixtures
	$(PY) -m tools.bbc.cycle_reference
	verilator --cc --exe --build -j $(VERILATOR_JOBS) -Wno-fatal --top-module BbcCpu \
	  -CFLAGS '-O3 -std=c++17 -Werror=return-type -iquote$(CURDIR)/tools/bbc/reference' -MAKEFLAGS 'OPT_FAST=-O3' --Mdir build/bbc/cpu \
	  rtl/bbc/BbcCpu.v build/bbc/ArletCpuEnabled.v third_party/6502/ALU.v \
	  $(CURDIR)/sim/native/bbc_cpu_main.cpp $(CURDIR)/sim/native/bbc_cpu_test.cpp \
	  $(CURDIR)/sim/native/bbc_cpu_timing_test.cpp $(CURDIR)/sim/native/bbc_cpu_interrupt_test.cpp \
	  $(CURDIR)/sim/native/bbc_cpu_bus_test.cpp $(CURDIR)/sim/native/bbc_cpu_official_bus_test.cpp \
	  $(CURDIR)/sim/native/bbc_cpu_interrupt_bus_test.cpp
	build/bbc/cpu/VBbcCpu reset build/bbc/reference-cycle/reset-bus.tsv
	build/bbc/cpu/VBbcCpu bus build/bbc/reference-cycle/bus.tsv
	build/bbc/cpu/VBbcCpu bus build/bbc/reference-cycle/bus.tsv 16
	build/bbc/cpu/VBbcCpu official-bus build/bbc/reference-cycle/official-bus.tsv
	build/bbc/cpu/VBbcCpu official-bus build/bbc/reference-cycle/official-bus.tsv 16
	build/bbc/cpu/VBbcCpu official-bus build/bbc/reference-cycle/official-bus.tsv 48
	build/bbc/cpu/VBbcCpu interrupt
	build/bbc/cpu/VBbcCpu interrupt-bus build/bbc/reference-cycle/irq-bus.tsv irq 1
	build/bbc/cpu/VBbcCpu interrupt-bus build/bbc/reference-cycle/irq-bus.tsv irq 16
	build/bbc/cpu/VBbcCpu interrupt-bus build/bbc/reference-cycle/irq-bus.tsv irq 48
	build/bbc/cpu/VBbcCpu interrupt-bus build/bbc/reference-cycle/nmi-bus.tsv nmi 1
	build/bbc/cpu/VBbcCpu interrupt-bus build/bbc/reference-cycle/nmi-bus.tsv nmi 16
	build/bbc/cpu/VBbcCpu interrupt-bus build/bbc/reference-cycle/nmi-bus.tsv nmi 48
	build/bbc/cpu/VBbcCpu timing build/bbc/cpu-irq.tsv irq
	build/bbc/cpu/VBbcCpu timing build/bbc/cpu-nmi.tsv nmi
	build/bbc/cpu/VBbcCpu timing build/bbc/cpu-irq.tsv irq 16
	build/bbc/cpu/VBbcCpu timing build/bbc/cpu-nmi.tsv nmi 16
	build/bbc/cpu/VBbcCpu functional build/bbc/6502_functional_test.bin
	build/bbc/cpu/VBbcCpu functional build/bbc/6502_functional_test.bin 3

bbc-rtl-check: bbc-gen
	cd build/bbc && yosys -p 'read_verilog -noautowire -sv ../../rtl/bbc/BbcCpu.v ArletCpuEnabled.v ../../third_party/6502/ALU.v BbcSystemTop.v; hierarchy -check -top BbcSystemTop; proc; opt; check -assert'

bbc-test: bbc-cpu-test bbc-cpu-brk-test bbc-cpu-decimal-transitions-test bbc-cpu-sequences-test bbc-cpu-streams-test bbc-cpu-stack-streams-test bbc-disc-test bbc-via-test bbc-adc-test bbc-system-test bbc-crtc-test bbc-lightpen-test bbc-video-test bbc-cursor-test bbc-teletext-test bbc-audio-test bbc-psg-sweep-test bbc-rtl-check
	$(SBT) 'testOnly leet.sim.BbcViaSim leet.sim.BbcVideoSim leet.sim.BbcDiscSim leet.sim.BbcRamSim leet.sim.BbcAdcSim'

bbc-validate: bbc-build
	$(PY) -m tools.bbc.run $(BBC_VALIDATION_ARGS)

.PHONY: bbc-validate96
bbc-validate96: bbc-build96 bbc-reference-test
	$(PY) -m tools.bbc.adc_results
	$(PY) -m tools.bbc.isolated --clock-multiplier 3 --matrix-only --trace-bus --prefer-profile96 $(BBC_VALIDATION_ARGS)

bbc-isolated: bbc-build
	$(PY) -m tools.bbc.isolated -- $(BBC_VALIDATION_ARGS)

.PHONY: bbc-reuse-boot
bbc-reuse-boot:
	$(PY) -m tools.bbc.boot_reuse "$(BBC_BOOT_BUNDLE)"

bbc-recheck:
	$(PY) -m tools.validation.bbc_recheck "$(BBC_RECHECK_RUN)" $(BBC_RECHECK_ARGS)

bbc-recompare:
	$(PY) -m tools.validation.bbc_recompare --run "$(BBC_RECOMPARE_RUN)" $(BBC_RECOMPARE_ARGS)

bbc-synth: bbc-gen
	cd build/bbc && yosys -p 'read_verilog -noautowire -sv ../../rtl/bbc/BbcCpu.v ArletCpuEnabled.v ../../third_party/6502/ALU.v BbcSystemTop.v; synth_ecp5 -top BbcSystemTop; stat; check -assert'

bbc-synth96: bbc-prepare
	$(PY) -c 'from tools.bbc.video96 import generate; generate()'
	cd build/bbc/full-video && yosys -p 'read_verilog -noautowire -sv ../../../rtl/bbc/BbcCpu.v ../ArletCpuEnabled.v ../../../third_party/6502/ALU.v BbcSystem96.v; synth_ecp5 -top BbcSystem96; stat; check -assert'

.PHONY: bbc-cpu-documented-induction-proof bbc-cpu-enable-composition-proof
bbc-cpu-documented-induction-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-documented-induction-proof

bbc-cpu-enable-composition-proof: bbc-cpu-documented-induction-proof bbc-cpu-idle-proof
	$(PY) -m tools.bbc.proofs.enable_composition --stream cpu-documented-induction-proof

.PHONY: bbc-cpu-stream-support
bbc-cpu-stream-support: bbc-gen
	$(PY) -m tools.bbc.proofs.support_cache

.PHONY: bbc-cpu-interrupt-stream-proof bbc-cpu-documented-irq-stream-proof bbc-cpu-documented-interrupt-stream-proof
bbc-cpu-interrupt-stream-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-interrupt-stream-proof

bbc-cpu-documented-irq-stream-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-documented-irq-stream-proof

bbc-cpu-documented-interrupt-stream-proof: bbc-gen bbc-cpu-stream-support
	$(PY) -m tools.bbc.proofs.cached cpu-documented-interrupt-stream-proof

.PHONY: bbc-cpu-nmi-idle-summary
bbc-cpu-nmi-idle-summary: bbc-gen
	$(PY) -m tools.bbc.proofs.nmi_idle_summary

.PHONY: bbc-cpu-nmi-macro-transition-proof
bbc-cpu-nmi-macro-transition-proof: bbc-gen
	$(PY) -m tools.bbc.proofs.nmi_macro_transition

.PHONY: bbc-cpu-restart-control-proof
bbc-cpu-restart-control-proof:
	$(PY) -m tools.bbc.proofs.restart_control

.PHONY: bbc-cpu-restart-idle-summary bbc-cpu-restart-macro-transition-proof bbc-cpu-nmi-enable-composition-proof
bbc-cpu-restart-idle-summary: bbc-gen
	$(PY) -m tools.bbc.proofs.restart_idle_summary

bbc-cpu-restart-macro-transition-proof: bbc-gen
	$(PY) -m tools.bbc.proofs.nmi_macro_transition --restart

# The composed theorem checks the separately sealed all-151 stream premise.
bbc-cpu-nmi-enable-composition-proof:
	$(PY) -m tools.bbc.proofs.nmi_enable_composition

.PHONY: bbc-cpu-cold-nmi-enable-composition-proof
bbc-cpu-cold-nmi-enable-composition-proof:
	$(PY) -m tools.bbc.proofs.nmi_enable_composition --cold

.PHONY: bbc-cpu-warm-nmi-enable-composition-proof bbc-video-palette-contract bbc-cpu-request-deadline-proof
bbc-cpu-warm-nmi-enable-composition-proof:
	$(PY) -m tools.bbc.proofs.restart_enable_composition

# Requires current common-clock video elaboration; checks its complete receipt.
bbc-video-palette-contract:
	$(PY) -m tools.bbc.proofs.video_palette --clock-multiplier 1
	$(PY) -m tools.bbc.proofs.video_palette

bbc-cpu-request-deadline-proof:
	$(PY) -m tools.bbc.proofs.cpu_request_deadline

.PHONY: bbc-cpu-reset-read-bridge
bbc-cpu-reset-read-bridge:
	$(PY) -m tools.bbc.proofs.reset_read_bridge

.PHONY: bbc-cpu-cold-warm-nmi-enable-composition-proof
bbc-cpu-cold-warm-nmi-enable-composition-proof:
	$(PY) -m tools.bbc.proofs.restart_enable_composition --cold

.PHONY: bbc-memory-response-deadline-proof bbc-memory-response-composition
bbc-memory-response-deadline-proof:
	$(PY) -m tools.bbc.proofs.memory_deadline

bbc-memory-response-composition:
	$(PY) -m tools.bbc.proofs.memory_composition

.PHONY: bbc-os-startup-zero-flag-bridge bbc-os-startup-zero-flag-fault
bbc-os-startup-zero-flag-bridge:
	$(PY) -m tools.bbc.proofs.os_startup_flags

bbc-os-startup-zero-flag-fault:
	$(PY) -m tools.bbc.proofs.os_startup_fault

.PHONY: bbc-os-startup-zero-flag-waits bbc-os-startup-zero-flag-waits-fault bbc-startup-interrupt-quiet-proof bbc-os-startup-profile-composition
bbc-os-startup-zero-flag-waits:
	$(PY) -m tools.bbc.proofs.os_startup_waits

bbc-os-startup-zero-flag-waits-fault:
	$(PY) -m tools.bbc.proofs.os_startup_fault --waits

bbc-startup-interrupt-quiet-proof:
	$(PY) -m tools.bbc.proofs.startup_interrupts

bbc-os-startup-profile-composition:
	$(PY) -m tools.bbc.proofs.startup_composition

.PHONY: bbc-video-cursor-segment-proof
bbc-video-cursor-segment-proof:
	$(PY) -m tools.bbc.proofs.video_cursor --clock-multiplier 1
	$(PY) -m tools.bbc.proofs.video_cursor

.PHONY: bbc-via-interrupt-state-proof
bbc-via-interrupt-state-proof:
	$(PY) -m tools.bbc.proofs.via_irq

.PHONY: bbc-video-register-proof
bbc-video-register-proof:
	$(PY) -m tools.bbc.proofs.video_bus --clock-multiplier 1
	$(PY) -m tools.bbc.proofs.video_bus

.PHONY: bbc-peripheral-pin-binding-proof
bbc-peripheral-pin-binding-proof:
	$(PY) -m tools.bbc.proofs.pin_bindings

.PHONY: bbc-video-memory-response-deadline-proof bbc-video-datapath-composition
bbc-video-memory-response-deadline-proof:
	$(PY) -m tools.bbc.proofs.video_memory_deadline

bbc-video-datapath-composition:
	$(PY) -m tools.bbc.proofs.video_composition

.PHONY: bbc-crtc-history-contract
bbc-crtc-history-contract:
	$(PY) -m tools.bbc.proofs.crtc_history

.PHONY: bbc-bitmap-history-contract bbc-teletext-attribute-history-contract bbc-teletext-font-response-contract bbc-teletext-glyph-contract bbc-teletext-font-deadline bbc-teletext-memory-deadline bbc-teletext-scanout-contract bbc-teletext-input-placement-contract bbc-video-history-composition
bbc-bitmap-history-contract:
	$(PY) -m tools.bbc.proofs.bitmap_history
	$(PY) -m tools.bbc.proofs.bitmap_history --clock-multiplier 3

bbc-teletext-attribute-history-contract:
	$(PY) -m tools.bbc.proofs.teletext_history

bbc-teletext-font-response-contract:
	$(PY) -m tools.bbc.proofs.teletext_font

bbc-teletext-glyph-contract:
	$(PY) -m tools.bbc.proofs.teletext_glyph

bbc-teletext-font-deadline:
	$(PY) -m tools.bbc.proofs.teletext_deadline

bbc-teletext-memory-deadline:
	$(PY) -m tools.bbc.proofs.teletext_memory_deadline

bbc-teletext-scanout-contract:
	$(PY) -m tools.bbc.proofs.teletext_scanout

bbc-teletext-input-placement-contract:
	$(PY) -m tools.bbc.proofs.teletext_inputs

bbc-video-history-composition:
	$(PY) -m tools.bbc.proofs.video_history_composition

.PHONY: bbc-c-reference-adapter-check bbc-c-scalar-architecture-bridge
bbc-c-reference-adapter-check:
	$(PY) -m tools.bbc.proofs.cpu_reference.compare

bbc-c-scalar-architecture-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.architecture

.PHONY: bbc-via-control-history-proof bbc-c-instruction-bridge bbc-c-instruction-bridge-faults bbc-c-cold-entry-bridge
bbc-via-control-history-proof:
	$(PY) -m tools.bbc.proofs.via_control_history

bbc-c-instruction-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.instructions

bbc-c-instruction-bridge-faults:
	$(PY) -m tools.bbc.proofs.cpu_reference.instruction_faults

bbc-c-cold-entry-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.cold_entry

.PHONY: bbc-c-structural-adapter-check bbc-c-structural-pruning-proof bbc-c-eligible-instruction-bridge bbc-c-cold-entry-profile
bbc-c-structural-adapter-check:
	$(PY) -m tools.bbc.proofs.cpu_reference.structural_compare

bbc-c-structural-pruning-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.pruning_proof

bbc-c-eligible-instruction-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.eligible_instructions

bbc-c-cold-entry-profile:
	$(PY) -m tools.bbc.proofs.cpu_reference.cold_profile

.PHONY: bbc-c-fetch-boundaries bbc-c-boundary-faults bbc-c-quiet-stream-bridge bbc-c-full-quiet-stream bbc-adc-scheduler-bridge bbc-c-protocol-history bbc-c-protocol-bridge bbc-c-full-protocol
bbc-c-fetch-boundaries:
	$(PY) -m tools.bbc.proofs.cpu_reference.fetch_boundaries

bbc-c-boundary-faults:
	$(PY) -m tools.bbc.proofs.cpu_reference.boundary_faults

bbc-c-quiet-stream-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.quiet_stream

bbc-c-full-quiet-stream:
	$(PY) -m tools.bbc.proofs.cpu_reference.full_quiet_stream

bbc-adc-scheduler-bridge:
	$(PY) -m tools.bbc.proofs.adc_scheduler

bbc-c-protocol-history:
	$(PY) -m tools.bbc.proofs.cpu_reference.protocol_history

bbc-c-protocol-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.protocol_bridge

bbc-c-full-protocol:
	$(PY) -m tools.bbc.proofs.cpu_reference.full_protocol

.PHONY: bbc-c-short-irq-history bbc-c-short-irq-bridge bbc-c-full-short-irq
bbc-c-short-irq-history:
	$(PY) -m tools.bbc.proofs.cpu_reference.short_irq

bbc-c-short-irq-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.short_irq_checks

bbc-c-full-short-irq:
	$(PY) -m tools.bbc.proofs.cpu_reference.full_short_irq

.PHONY: bbc-c-branch-interrupt-history bbc-c-branch-interrupt-bridge bbc-c-full-branch-interrupts
bbc-c-branch-interrupt-history:
	$(PY) -m tools.bbc.proofs.cpu_reference.branch_interrupts

bbc-c-branch-interrupt-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.branch_interrupt_checks

bbc-c-full-branch-interrupts:
	$(PY) -m tools.bbc.proofs.cpu_reference.full_branch_interrupts

.PHONY: bbc-c-independent-translation bbc-c-zero-page-irq-history bbc-c-zero-page-irq-bridge bbc-c-memory-irq-history
bbc-c-independent-translation:
	$(PY) -m tools.bbc.proofs.cpu_reference.independent_translation --cbmc "$(BBC_CBMC)"

bbc-c-zero-page-irq-history:
	$(PY) -m tools.bbc.proofs.cpu_reference.zero_page_interrupts

bbc-c-zero-page-irq-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.zero_page_interrupt_checks

bbc-c-memory-irq-history:
	$(PY) -m tools.bbc.proofs.cpu_reference.memory_interrupts

.PHONY: bbc-c-documented-model bbc-c-documented-model-checks bbc-c-documented-memory
bbc-c-documented-model:
	$(PY) -m tools.bbc.proofs.cpu_reference.documented_model

bbc-c-documented-model-checks:
	$(PY) -m tools.bbc.proofs.cpu_reference.documented_checks

bbc-c-documented-memory:
	$(PY) -m tools.bbc.proofs.cpu_reference.documented_memory

.PHONY: bbc-c-memory-irq-bridge bbc-c-memory-domain-checks bbc-c-control-histories bbc-c-control-induction
bbc-c-memory-irq-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.memory_interrupt_checks

bbc-c-memory-domain-checks:
	$(PY) -m tools.bbc.proofs.cpu_reference.memory_domain_checks

bbc-c-control-histories:
	$(PY) -m tools.bbc.proofs.cpu_reference.control_histories

bbc-c-control-induction:
	$(PY) -m tools.bbc.proofs.cpu_reference.control_induction

.PHONY: bbc-c-full-official-irq-bridge
bbc-c-full-official-irq-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.control_interrupt_checks

.PHONY: bbc-c-warm-histories
bbc-c-warm-histories:
	$(PY) -m tools.bbc.proofs.cpu_reference.warm_histories

.PHONY: bbc-c-warm-checks bbc-c-warm-mixed
bbc-c-warm-checks:
	$(PY) -m tools.bbc.proofs.cpu_reference.warm_checks

bbc-c-warm-mixed:
	$(PY) -m tools.bbc.proofs.cpu_reference.warm_mixed $(BBC_WARM_MIXED_ARGS)

.PHONY: bbc-c-reset-step-bridge
bbc-c-reset-step-bridge:
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_steps

.PHONY: bbc-c-reset-sequence bbc-c-reset-fetch-membership bbc-c-fetch-seed-investigation bbc-c-fetch-seed-checks bbc-c-fetch-seed-domain
bbc-c-reset-sequence:
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_sequence

bbc-c-reset-fetch-membership:
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_fetch_handoff

bbc-c-fetch-seed-investigation:
	$(PY) -m tools.bbc.proofs.cpu_reference.fetch_seed

bbc-c-fetch-seed-checks:
	$(PY) -m tools.bbc.proofs.cpu_reference.fetch_seed_checks

bbc-c-fetch-seed-domain:
	$(PY) -m tools.bbc.proofs.cpu_reference.fetch_seed_domain

.PHONY: bbc-c-os-startup-profile bbc-c-os-startup-induction
bbc-c-os-startup-profile:
	$(PY) -m tools.bbc.proofs.cpu_reference.os_startup_profile

bbc-c-os-startup-induction:
	$(PY) -m tools.bbc.proofs.cpu_reference.os_startup_induction

.PHONY: bbc-c-fetch-pin-normalization bbc-c-stored-pin-normalization
bbc-c-fetch-pin-normalization:
	$(PY) -m tools.bbc.proofs.cpu_reference.fetch_pin_normalization

bbc-c-stored-pin-normalization:
	$(PY) -m tools.bbc.proofs.cpu_reference.stored_pin_normalization

# Standalone device checks leave whole-machine builds used by captures alone.
.PHONY: bbc-serial-component-test
bbc-serial-component-test:
	$(PY) -m tools.bbc.serial_probe
	$(PY) -m tools.bbc.serial_state_proof --component-only

# Current-source proofs and a separate simulator; active captures stay intact.
BBC_CURRENT_REPLAY_CYCLES ?= 64000000
.PHONY: bbc-current-gen bbc-source-reset-rtl-proof bbc-source-warm-startup-rtl-proof bbc-display-source-history-proof bbc-crtc-source-history-proof bbc-current-integration-proof bbc-current-test
.PHONY: bbc-c-source-foundation bbc-source-history-proof bbc-source-reset-history-handoff bbc-source-cpu-proof
.PHONY: bbc-source-history-faults bbc-c-source-profile-proof bbc-source-mixed-rtl-test
.PHONY: bbc-source-reset-history-composition
.PHONY: bbc-source-nmi-sampling-proof bbc-source-physical-mixed-rtl-test
.PHONY: bbc-source-macro-mixed-rtl-test bbc-video-source-clock-proof
.PHONY: bbc-source-warm-mixed-rtl-test bbc-video-parent-phase-proof
bbc-source-warm-mixed-rtl-test:
	$(PY) -m tools.bbc.proofs.cpu_reference.warm_mixed_rtl
bbc-video-parent-phase-proof: bbc-video-source-clock-proof
	$(PY) -m tools.bbc.proofs.video_parent_phase
bbc-video-source-clock-proof:
	$(PY) -m tools.bbc.proofs.video_source_clock
bbc-source-macro-mixed-rtl-test: bbc-source-nmi-sampling-proof
	$(PY) -m tools.bbc.proofs.nmi_macro_transition
	$(PY) -m tools.bbc.proofs.cpu_reference.macro_mixed_rtl
bbc-source-nmi-sampling-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.nmi_sampling
bbc-source-physical-mixed-rtl-test: bbc-source-nmi-sampling-proof
	$(PY) -m tools.bbc.proofs.cpu_reference.physical_mixed_rtl
bbc-source-reset-history-composition:
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_history_composition
bbc-source-mixed-rtl-test:
	$(PY) -m tools.bbc.proofs.cpu_reference.mixed_rtl
bbc-c-source-profile-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.source_profile
.PHONY: bbc-hsync-source-history-proof
bbc-hsync-source-history-proof:
	$(PY) -m tools.bbc.proofs.hsync_source_history

bbc-c-source-foundation:
	$(PY) -m tools.bbc.proofs.cpu_reference.foundation --cbmc "$(BBC_CBMC)" --jobs $(BBC_C_SOURCE_JOBS)

# Local checks reuse a sealed foundation; this target refreshes the full chain.
bbc-source-cpu-proof: bbc-c-source-foundation
	$(PY) -m tools.bbc.proofs.cpu_reference.source_profile
	$(PY) -m tools.bbc.proofs.cpu_reference.source_history --jobs $(BBC_C_SOURCE_JOBS)
	$(PY) -m tools.bbc.proofs.cpu_reference.source_history_faults --jobs $(BBC_C_SOURCE_JOBS)
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_history_handoff
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_history_composition
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_rtl
	$(PY) -m tools.bbc.proofs.cpu_reference.warm_startup_rtl

bbc-source-history-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.source_history --jobs $(BBC_C_SOURCE_JOBS)

bbc-source-history-faults:
	$(PY) -m tools.bbc.proofs.cpu_reference.source_history_faults --jobs $(BBC_C_SOURCE_JOBS)

bbc-source-reset-history-handoff:
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_history_handoff

bbc-current-gen:
	$(PY) -m tools.bbc.proofs.current_machine --generate-only

bbc-source-reset-rtl-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_rtl

bbc-display-source-history-proof: bbc-current-gen
	$(PY) -m tools.bbc.proofs.display_history

bbc-crtc-source-history-proof:
	$(PY) -m tools.bbc.proofs.crtc_source_history

bbc-source-warm-startup-rtl-proof: bbc-source-reset-rtl-proof
	$(PY) -m tools.bbc.proofs.cpu_reference.warm_startup_rtl

bbc-current-integration-proof: bbc-source-cpu-explicit-events bbc-source-warm-startup-rtl-proof bbc-display-source-history-proof bbc-crtc-source-history-proof
	$(PY) -m tools.bbc.proofs.current_machine

bbc-current-test: bbc-current-integration-proof bbc-build96 bbc-capture-reference
	$(PY) -m tools.bbc.cold_machine_video --cycles $(BBC_CURRENT_REPLAY_CYCLES) $(BBC_COLD_VIDEO_ARGS)

# Event-history preparation is deliberately separate from the short composition loop.
.PHONY: bbc-source-event-history-proof bbc-source-rtl-event-history-proof
.PHONY: bbc-source-lda-handoff-proof bbc-source-cold-event-history-proof
.PHONY: bbc-source-event-composition bbc-source-parent-schedule
bbc-source-event-history-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.event_history

bbc-source-rtl-event-history-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.rtl_event_history

bbc-source-lda-handoff-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.lda_handoff

bbc-source-cold-event-history-proof:
	$(PY) -m tools.bbc.proofs.cpu_reference.cold_lda_event_history

bbc-source-event-composition:
	$(PY) -m tools.bbc.proofs.cpu_reference.event_composition

bbc-source-parent-schedule: bbc-source-event-composition
	$(PY) -m tools.bbc.proofs.cpu_reference.parent_schedule

.PHONY: bbc-source-response-settlement
bbc-source-response-settlement: bbc-source-parent-schedule
	$(PY) -m tools.bbc.proofs.cpu_reference.response_settlement

.PHONY: bbc-source-callback-order
bbc-source-callback-order: bbc-current-gen bbc-source-fdc-order
	$(PY) -m tools.bbc.proofs.cpu_reference.callback_order

.PHONY: bbc-source-zero-dispatch bbc-source-callback-summary bbc-source-device-epoch
bbc-source-zero-dispatch: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.zero_dispatch

# Refresh local stage and finite command evidence; the CPU composition receipt
# remains a separately prepared/audited premise, as with callback-order.
bbc-source-device-epoch: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.device_epoch

bbc-source-callback-summary: bbc-source-callback-order bbc-source-zero-dispatch bbc-source-device-epoch bbc-source-fdc-reset
	$(PY) -m tools.bbc.proofs.cpu_reference.callback_summary

# Consume sealed prerequisites; regenerate slow history/device premises separately.
.PHONY: bbc-source-callback-cpu
bbc-source-callback-cpu:
	$(PY) -m tools.bbc.proofs.cpu_reference.callback_cpu

.PHONY: bbc-source-event-capture
bbc-source-event-capture:
	$(PY) -m tools.bbc.proofs.cpu_reference.source_events

.PHONY: bbc-source-device-events
bbc-source-device-events: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_events

.PHONY: bbc-source-cpu-explicit-events
bbc-source-cpu-explicit-events: bbc-prepare
	$(PY) -m tools.bbc.proofs.cpu_reference.explicit_events

.PHONY: bbc-source-fdc-reset
bbc-source-fdc-reset: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_reset

.PHONY: bbc-source-fdc-commands
bbc-source-fdc-commands: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_commands

.PHONY: bbc-source-fdc-timers
bbc-source-fdc-timers: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_timers

.PHONY: bbc-source-fdc-next-five
bbc-source-fdc-next-five: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_next5

.PHONY: bbc-source-fdc-shared
bbc-source-fdc-shared: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_shared

.PHONY: bbc-source-fdc-order
bbc-source-fdc-order: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_order

.PHONY: bbc-source-fdc-bits
bbc-source-fdc-bits: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_bits

.PHONY: bbc-source-fdc-sync
bbc-source-fdc-sync: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_sync

.PHONY: bbc-source-fdc-concurrent
bbc-source-fdc-concurrent: bbc-current-gen bbc-source-fdc-sync
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_concurrent

.PHONY: bbc-source-fdc-byte
bbc-source-fdc-byte: bbc-current-gen
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_byte

.PHONY: bbc-source-fdc-crc
bbc-source-fdc-crc: bbc-current-gen bbc-source-fdc-byte bbc-source-fdc-bits
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_crc

.PHONY: bbc-source-fdc-marker
bbc-source-fdc-marker: bbc-current-gen bbc-source-fdc-byte bbc-source-fdc-bits
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_marker

.PHONY: bbc-source-fdc-completion
bbc-source-fdc-completion: bbc-current-gen bbc-source-fdc-byte
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_completion

.PHONY: bbc-source-fdc-ready
bbc-source-fdc-ready: bbc-current-gen bbc-source-fdc-byte bbc-source-fdc-completion
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_ready

.PHONY: bbc-source-fdc-gap
bbc-source-fdc-gap: bbc-current-gen bbc-source-fdc-byte bbc-source-fdc-completion bbc-source-fdc-ready bbc-source-fdc-marker
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_gap

.PHONY: bbc-source-fdc-write-gap
bbc-source-fdc-write-gap: bbc-current-gen bbc-source-fdc-byte bbc-source-fdc-gap
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_write_gap

.PHONY: bbc-source-fdc-pending-irq
bbc-source-fdc-pending-irq: bbc-current-gen bbc-source-fdc-byte bbc-source-fdc-gap bbc-source-fdc-write-gap bbc-source-fdc-marker
	$(PY) -m tools.bbc.proofs.cpu_reference.fdc_pending_irq

.PHONY: bbc-source-cold-event-composition bbc-reset-release-proof bbc-source-physical-warm-rtl-test
.PHONY: bbc-source-macro-warm-rtl-test
.PHONY: bbc-reference-bus-epoch-proof bbc-disc-nmi-order-test bbc-hsync-observability-proof
.PHONY: bbc-reference-timing-composition
bbc-reset-release-proof: bbc-cpu-nmi-idle-summary
	$(PY) -m tools.bbc.proofs.cpu_reference.reset_release

bbc-source-cold-event-composition: bbc-reset-release-proof
	$(PY) -m tools.bbc.proofs.cpu_reference.cold_event_composition

bbc-source-physical-warm-rtl-test: bbc-source-mixed-rtl-test bbc-source-nmi-sampling-proof
	$(PY) -m tools.bbc.proofs.cpu_reference.physical_mixed_rtl --warm

bbc-source-macro-warm-rtl-test: bbc-source-macro-mixed-rtl-test bbc-cpu-restart-idle-summary bbc-cpu-restart-macro-transition-proof
	$(PY) -m tools.bbc.proofs.cpu_reference.macro_warm_rtl

bbc-reference-bus-epoch-proof: bbc-current-gen
	$(PY) -m tools.bbc.proofs.reference_bus_epoch

bbc-disc-nmi-order-test: bbc-current-gen
	$(PY) -m tools.bbc.proofs.disc_nmi_order

bbc-hsync-observability-proof: bbc-current-gen
	$(PY) -m tools.bbc.proofs.hsync_observability

bbc-reference-timing-composition: bbc-reference-bus-epoch-proof bbc-video-parent-phase-proof bbc-disc-nmi-order-test bbc-peripheral-pin-binding-proof bbc-via-interrupt-state-proof bbc-sound-bus-state-proof bbc-psg-state-proof
	$(PY) -m tools.bbc.proofs.reference_timing_composition
