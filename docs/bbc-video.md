# BBC video

`BbcVideo` combines the Hitachi 6845 CRTC, bitmap ULA and optional SAA5050
teletext. The 32 MHz machine flags teletext as unsupported. `BbcSystem96`
uses a common 96 MHz master clock, preserving the original CPU/device enables
and emitting exact 12 MHz teletext dots every eight master cycles.

## Bitmap, CRTC and light pen

RAM reads use the physical raster address, including odd-field RA0 in interlace
video. The palette uses distributed RAM and resettable validity bits. Software
reads CRTC registers 12–17, and the light pen latches its address through the
system VIA CB2 edge. With no light-pen strobe, registers 16–17 stay zero.

| Target                                                     | Comparison                                                                                                                        |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `bbc-crtc-test`, `bbc-crtc-transition-test`                | Register access and counters, including live writes and external pin endpoints.                                                   |
| `bbc-video-test`, `bbc-video96-test`                       | Bitmap pixels at both character rates and interlace modes. The 96 MHz target also tests teletext.                                 |
| `bbc-video-transition-test`, `bbc-video96-transition-test` | Live ULA mode, clock and palette changes.                                                                                         |
| `bbc-cursor-test`, `bbc-cursor96-test`                     | Bitmap cursor output, including the 298-case, 37,838,848-pixel corpus at 96 MHz.                                                  |
| `bbc-lightpen-test`                                        | Actual VIA/video wiring across 1,536 cases and 1,179,648 observations.                                                            |
| `bbc-video-proof`                                          | Preservation of default 32 MHz outputs relative to the pinned baseline, with the named display corrections applied to both sides. |

The independent C RAM-address fixture covers 4,194,304 combinations. The bitmap
JavaScript fixture supplies physical RA0 at its RAM callback. The unmodified
JavaScript bitmap path omits odd-field RA0.

## Teletext

The IC15 latch feeds the chip's three character stages. Character clocks
continue through blanking. The input gate forces bit 6 in blanked characters
before they enter the chip, and the character clock precedes the pipelined row
reset. The clocked dual-port font ROM has three master edges from character
processing to dot load.

| Target                                                              | Comparison                                                                                                                                              |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bbc-teletext-test`                                                 | Original attribute, glyph and cell behavior, including 515,904 cell observations.                                                                       |
| `bbc-teletext-proof`, `bbc-video96-font-proof`                      | Preservation against the row-ROM baseline and earlier integrated font path.                                                                             |
| `bbc-video96-test`                                                  | 960,000 settled teletext dots against unchanged beebjit code, across both field parities and display skews 0/1.                                         |
| `bbc-teletext-cursor-test`                                          | 614,400 dots from 64 cursor cases.                                                                                                                      |
| `bbc-teletext-cursor-corners-test`, `bbc-teletext-cursor-cold-test` | 5,775,360 dots across 512 finite cases, including borders, blink modes and live control characters. The cold target uses the original startup schedule. |
| `bbc-video96-reference`                                             | Diagnostic reproduction of the 1,106-dot disagreement with the unmodified JavaScript model, which omits blanked character clocks.                       |

Corner fixtures can restore DUT-owned neutral cursor prefixes at VSYNC and then
execute the full cursor programming sequence. The C reference still starts cold.
Proofs for local display history and parent phase are listed in
[reference bridges](bbc-reference-bridges.md#video-and-parent-time).

Standard 1 MHz teletext is supported. Arbitrary mode changes, 2 MHz teletext
tricks, every cursor/border phase and physical LOSE/DEW timing remain outside
these checks. See [BBC runtime](bbc-runtime.md#gameplay-replay) for whole-machine
replay and [mapping estimates](../README.md#fpga-resource-estimates).
