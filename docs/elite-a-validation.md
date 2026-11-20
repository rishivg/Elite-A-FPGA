# PicoRV32 demo validation

The demo has simplified gameplay, a 128×128 monochrome render surface and
separate original-byte renderer kernels. The [BBC runtime](bbc-runtime.md)
runs the released game and ROMs on BBC RTL.

| Command                  | Purpose                                                                                                         |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- |
| `make validation-build`  | Build native demo comparators and failure probes.                                                               |
| `make validation-video`  | Compare the playable SoC's presented frame with its digital scanout and check input, audio activity and reboot. |
| `make validation-render` | Compare renderer stages, connected pipelines and PicoRV32 MMIO against executed 6502 routines.                  |
| `make validate-elite-a`  | Compare the demo and original game twice per scenario. This reports known gameplay differences.                 |
| `make validation-audio`  | Compare independent PSG probes, including the disputed zero-period tone-2 cases.                                |

`tools/validation/reference.lock.json` pins the disc, ROMs, original source and
emulator. Setup verifies all 29 released files and installs the locked emulator
dependencies. For a focused replay, pass
`VALIDATION_ARGS='--scenario initial-status --repeat 1'`.

## Results

Open `build/validation/latest/report.html`. The manifest records inputs,
firmware/RTL hashes, tools and symbol maps. Each scenario keeps input times,
checkpoint RAM and state, images, PSG activity and repeated captures.

The reference boots the released NEWCOME commander in an Adder at Lave. The
demo's JAMESON/Cobra start, market and world state differ. The original 256×248
active color frame fails the demo's resolution check. The audio comparison
requires exact timestamped PSG events. In standalone audio tests, two
zero-period tone-2 noise cases disagree with jsbeeb. Physical noise behavior,
absolute phase and analogue output still need measurement.

The original disc supplies 38 named blueprints, 43 geometry variants and 398
bank/role instances. Reference fixtures preserve the released Splinter's
incorrect pointers. One bank's face pointer reaches runtime memory outside the
file and remains unresolved.

## Released renderer kernels

`make validation-render` verifies the flight overlay symbols against the
released `1.F` bytes and executes LL17, LL42, LL51 and LL9 in the independent
BBC reference. Read-only flight observations and isolated boundary operands
produce distinct fixtures. The RTL checks preserve signed-magnitude truncation,
the low bits of sign bytes, wraparound, shared scaling and original lossy
division.

| Stage                           | Recorded case count |
| ------------------------------- | ------------------: |
| Orientation and transpose       |              65,841 |
| LL51 transform                  |              82,549 |
| Face visibility                 |              73,454 |
| Vertex translation and scaling  |              78,788 |
| Projection                      |              65,536 |
| Connected orientation-to-screen |                 652 |
| Actual PicoRV32 MMIO            |                 682 |

`fw/mmio.h` defines the register ABI. Face operands/results occupy
`0x64–0x74`, transforms use `0x78–0x9C`, and world inputs and projected
results use `0xA0–0xB4`. Busy ownership, operand transfer and reset are covered
in the production register path. The sealed fixtures and comparison report are
under `build/validation/render-latest/`.

## Physical validation

The demo still needs original gameplay, blueprint integration, clipping,
raster rules and durable commander storage. Board work includes pin/clock
constraints, routed timing, capture on actual inputs and outputs, and TMDS
encoding for ULX3S GPDI. See the [demo data path](architecture.md#demo-and-renderer-dataflow).
