import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output, mode = "attributes", fontOutput] = process.argv.slice(2);
if (!["attributes", "cells"].includes(mode)) throw Error("Unknown teletext corpus");
const { Teletext } = await import(pathToFileURL(root + "/src/teletext.js"));
if (mode === "cells") {
  if (!fontOutput) throw Error("Missing font output");
  const { makeChars } = await import(pathToFileURL(root + "/src/teletext_data.js"));
  const font = makeChars(),
    rows = [];
  for (let row = 0; row < 960; row++) {
    let value = 0;
    for (let x = 0; x < 6; x++) value |= font[row * 6 + x] << x;
    rows.push(value.toString(16).padStart(2, "0"));
  }
  fs.writeFileSync(fontOutput, rows.join("\n") + "\n");
}
const cases = [];
function corpus(run) {
  const chip = new Teletext();
  const records = [];
  const setId = (value) =>
    value === chip.normalGlyphs
      ? 0
      : value === chip.graphicsGlyphs
        ? 1
        : value === chip.separatedGlyphs
          ? 2
          : (() => {
              throw Error("Unknown glyph set");
            })();
  function step(operation, data, enable = true) {
    if (enable) {
      switch (operation) {
        case 0:
          break;
        case 1:
          chip.fetchData(data);
          break;
        case 2:
          chip.setDEW(!!(data & 1));
          break;
        case 3:
          chip.setDISPTMG(!!(data & 1));
          break;
        case 4:
          chip.setRA0(!!(data & 1));
          break;
        case 5:
          chip.advance();
          break;
        default:
          throw Error("Invalid operation");
      }
    }
    const suppressed =
      chip.cellConceal ||
      (chip.cellFlash && chip.hideFlashing) ||
      (chip.secondHalfOfDouble && !chip.dbl);
    const character = chip.cellGlyphIndex / 20 + 32;
    const cell =
      character |
      (setId(chip.curGlyphs) << 7) |
      (chip.glyphScanline() << 9) |
      (((chip.cellPalette >>> 2) & 7) << 14) |
      ((chip.cellPalette >>> 5) << 17) |
      (+suppressed << 20);
    const state =
      chip.col |
      (chip.bg << 3) |
      (+chip.sep << 6) |
      (+chip.dbl << 7) |
      (+chip.secondHalfOfDouble << 8) |
      (+chip.wasDbl << 9) |
      (+chip.gfx << 10) |
      (+chip.conceal << 11) |
      (+chip.flash << 12) |
      (+chip.holdChar << 13) |
      (chip.heldChar << 14) |
      (chip.scanlineCounter << 21) |
      (chip.flashTime << 25);
    const record = [operation | (data << 8) | (+enable << 16), cell >>> 0, state >>> 0];
    if (mode === "cells") {
      const weights = suppressed ? 0 : chip.curGlyphs[chip.cellGlyphIndex + chip.glyphScanline()];
      record.push(weights >>> 0);
    }
    records.push(record);
  }
  function byte(data) {
    step(1, data);
    step(5, 0);
  }
  function flush(data) {
    for (let i = 0; i < 4; i++) byte(data);
  }
  function line() {
    step(3, 1);
    step(3, 0);
  }
  run({ step, byte, flush, line });
  cases.push(records);
}
for (const graphics of [0, 1, 2])
  for (const doubleHeight of [false, true])
    for (const flashFrames of [0, 17])
      for (const lower of [false, true])
        for (const ra0 of [false, true]) {
          corpus(({ step, byte, flush, line }) => {
            for (let i = 0; i < flashFrames; i++) {
              step(2, 1);
              step(2, 0);
            }
            if (lower)
              for (let i = 0; i < 10; i++) {
                flush(13);
                line();
              }
            step(4, +ra0);
            flush(graphics ? 17 : 1);
            if (graphics === 2) flush(26);
            if (doubleHeight) flush(13);
            flush(8);
            flush(30);
            for (let i = 0; i < 256; i++) byte(i);
            for (let row = 0; row < 20; row++) {
              line();
              for (let i = 0; i < 128; i++) byte(i);
            }
          });
        }
// Every ordered control-code pair, with held graphics between the controls.
corpus(({ byte, flush, line }) => {
  for (let a = 0; a < 32; a++)
    for (let b = 0; b < 32; b++) {
      line();
      flush(17);
      flush(0x7f);
      flush(30);
      byte(a);
      byte(b);
      flush(32);
    }
});
// Arbitrary pin/clock interleavings include repeated levels and disabled clocks.
corpus(({ step }) => {
  let seed = 0x50506845;
  for (let i = 0; i < 200000; i++) {
    seed ^= seed << 13;
    seed ^= seed >>> 17;
    seed ^= seed << 5;
    const operation = (seed >>> 0) % 6;
    step(operation, (seed >>> 16) & 255, (seed & 15) !== 0);
  }
});
const fd = fs.openSync(output, "w");
const header = Buffer.alloc(12);
header.write(mode === "cells" ? "BBCCELL" : "BBCTTXT", 0, "ascii");
header[7] = 1;
header.writeUInt32LE(cases.length, 8);
fs.writeSync(fd, header);
let total = 0;
const stride = mode === "cells" ? 16 : 12;
for (let id = 0; id < cases.length; id++) {
  const records = cases[id],
    caseHeader = Buffer.alloc(8),
    data = Buffer.alloc(records.length * stride);
  caseHeader.writeUInt32LE(id);
  caseHeader.writeUInt32LE(records.length, 4);
  records.forEach((record, i) =>
    record.forEach((word, j) => data.writeUInt32LE(word, i * stride + j * 4)),
  );
  fs.writeSync(fd, caseHeader);
  fs.writeSync(fd, data);
  total += records.length;
}
fs.closeSync(fd);
console.log(JSON.stringify({ cases: cases.length, observations: total }));
