# PicoRV32 firmware, renderer simulations and desktop demo.

gen: fw
	$(SBT) "runMain leet.Generate"

# Renderer suites already execute in validation-render; run the rest once.
sim test: fw demo-build validation-render
	$(SBT) 'testOnly -- -l leet.sim.RenderKernel'

.PHONY: demo-build test-demo test-fast
demo-build: fw
	$(PY) -m tools.validation.demo_build

test-demo: demo-build
	$(SBT) 'testOnly -- -n leet.sim.DemoFrames'

test-fast: fw
	$(SBT) 'testOnly -- -l "leet.sim.DemoFrames leet.sim.RenderKernel"'

ship:
	$(PY) tools/shipgen.py data/ships/cobra_mk3.json build/data/cobra_mk3.mem

raster:
	$(SBT) "testOnly leet.sim.BresenhamSim"
	$(PY) tools/pbm2png.py build/sim/lines.pbm build/sim/lines.png 10

render:
	$(SBT) "testOnly leet.sim.ShipRenderSim"
	$(PY) tools/montage.py

fw:
	$(MAKE) -C fw

soc: fw
	$(SBT) "testOnly leet.sim.SocSim"

anim: demo-build
	$(SBT) "testOnly leet.sim.RenderSocSim"
	$(PY) tools/gif.py "build/sim/soc_ship_*.pbm" build/sim/soc_ship.gif 4 80

fly: demo-build
	$(SBT) "testOnly leet.sim.FlightSocSim"
	$(PY) tools/gif.py "build/sim/flight_*.pbm" build/sim/flight.gif 4 60

bubble: demo-build
	$(SBT) "testOnly leet.sim.BubbleSocSim"
	$(PY) tools/gif.py "build/sim/bubble_*.pbm" build/sim/bubble.gif 4 60

combat: demo-build
	$(SBT) "testOnly leet.sim.CombatSocSim"
	$(PY) tools/gif.py "build/sim/combat_*.pbm" build/sim/combat.gif 4 60

tactics: demo-build
	$(SBT) "testOnly leet.sim.TacticsSocSim"
	$(PY) tools/gif.py "build/sim/tactics_*.pbm" build/sim/tactics.gif 4 60

planet: demo-build
	$(SBT) "testOnly leet.sim.PlanetSocSim"
	$(PY) tools/gif.py "build/sim/planet_*.pbm" build/sim/planet.gif 4 60

vista: demo-build
	$(SBT) "testOnly leet.sim.VistaSocSim"
	$(PY) tools/gif.py "build/sim/vista_*.pbm" build/sim/vista.gif 4 60

hyper: demo-build
	$(SBT) "testOnly leet.sim.HyperspaceSocSim"
	$(PY) tools/gif.py "build/sim/hyper_*.pbm" build/sim/hyper.gif 4 60

market: demo-build
	$(SBT) "testOnly leet.sim.MarketSocSim"
	$(PY) tools/gif.py "build/sim/market_*.pbm" build/sim/market.gif 4 60

equip: demo-build
	$(SBT) "testOnly leet.sim.EquipSocSim"
	$(PY) tools/gif.py "build/sim/equip_*.pbm" build/sim/equip.gif 4 60

sysdata: demo-build
	$(SBT) "testOnly leet.sim.SysDataSocSim"
	$(PY) tools/gif.py "build/sim/sysdata_*.pbm" build/sim/sysdata.gif 4 60

missile: demo-build
	$(SBT) "testOnly leet.sim.MissileSocSim"
	$(PY) tools/gif.py "build/sim/missile_*.pbm" build/sim/missile.gif 4 60

# Interactive desktop build: verilate the real SoC and drive it with live
# keyboard input (sim/native/play.cpp). Run from build/rtl so the firmware
# $readmemb images resolve.
#   make play        -> opens a real graphical window (SDL2, -DUSE_SDL)
#   make play-smoke  -> headless N-frame self-check (no SDL/display)
SDL_CFLAGS := $(shell pkg-config --cflags sdl2 2>/dev/null)
SDL_LIBS   := $(shell pkg-config --libs sdl2 2>/dev/null)

play: fw gen
	@test -n "$(SDL_LIBS)" || { echo "SDL2 not found — install libsdl2-dev (sudo apt install libsdl2-dev)"; exit 1; }
	cd build/rtl && verilator --cc --exe --build -j $(VERILATOR_JOBS) -Wno-fatal --top-module Soc \
	  -CFLAGS "-O2 -std=c++17 -DUSE_SDL $(SDL_CFLAGS)" -LDFLAGS "$(SDL_LIBS)" \
	  -o play --Mdir obj_play Soc.v ../../third_party/picorv32.v ../../sim/native/play.cpp
	@echo "== fpgaleet — WASD fly, SPACE fire, X missile, C chart, M market, E equip, Q quit =="
	cd build/rtl && ./obj_play/play

play-smoke: fw gen
	cd build/rtl && verilator --cc --exe --build -j $(VERILATOR_JOBS) -Wno-fatal --top-module Soc \
	  -CFLAGS "-O2 -std=c++17" -o play --Mdir obj_play_smoke \
	  Soc.v ../../third_party/picorv32.v ../../sim/native/play.cpp
	cd build/rtl && ./obj_play_smoke/play --frames 96

verify:
	$(PY) -m tools.oracle.verify

lint: gen
	verilator --lint-only -Wall --top-module EliteTop sim/lint.vlt \
	  build/rtl/EliteTop.v third_party/picorv32.v

# Synthesis dry-run (resource estimate only; needs yosys). No nextpnr = no Fmax.
synth: gen
	cd build/rtl && yosys -p "read_verilog -sv ../../third_party/picorv32.v Soc.v; synth_ecp5 -top Soc; stat"
