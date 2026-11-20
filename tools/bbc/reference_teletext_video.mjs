// Observe the unchanged original SAA5050 through complete Video/CRTC operation.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const cases = [];
const text = [
  1, 69, 76, 73, 84, 69, 45, 65, 32, 2, 70, 80, 71, 65, 32, 19, 0x7f, 30, 26, 0x3f, 31, 32, 13, 65,
  66, 67, 12, 8, 65, 9, 24, 65, 7, 65, 29, 32, 28, 32,
];
for (const pattern of [0, 1]) {
  const fb = new Uint32Array(1024 * 625);
  const v = new Video(false, fb, () => {});
  v.reset(
    {
      videoRead: (address) =>
        pattern ? text[(address & 0x3ff) % text.length] : ((address * 17) ^ (address >>> 3)) & 127,
    },
    { setVBlankInt() {} },
  );
  let field = 0,
    step = 0;
  const pixels = [];
  const end = v.endOfFrame;
  v.endOfFrame = function () {
    end.call(this);
    field++;
  };
  const emit = v.teletext.emit;
  v.teletext.emit = function (buffer, offset) {
    emit.call(this, buffer, offset);
    if (field < 20 || field > 21) return;
    const hidden =
      this.cellConceal ||
      (this.cellFlash && this.hideFlashing) ||
      (this.secondHalfOfDouble && !this.dbl);
    const bits = hidden ? 0 : this.curGlyphs[this.cellGlyphIndex + this.glyphScanline()] >>> 0;
    const fg = (this.cellPalette >>> 2) & 7,
      bg = (this.cellPalette >>> 5) & 7;
    const dots = [];
    for (let group = 0; group < 4; group++) {
      const w = Array.from({ length: 4 }, (_, i) => (bits >>> (group * 8 + i * 2)) & 3);
      const a = w[0] / 3,
        c = w[3] / 3,
        b = (w[1] - a) / 2;
      if (![a, b, c].every((x) => x === 0 || x === 1) || w[2] !== 2 * b + c)
        throw Error("Original dot weights lost information");
      dots.push(a, b, c);
    }
    const y = (v.vertCounter * 10 + v.scanlineCounter / 2) * 2 + +!v.doEvenFrameLogic;
    for (let x = 0; x < 12; x++) {
      const color = dots[x] ? fg : bg;
      pixels.push([
        field,
        (v.horizCounter - 2) * 12 + x,
        y,
        color & 1 ? 255 : 0,
        color & 2 ? 255 : 0,
        color & 4 ? 255 : 0,
      ]);
    }
  };
  const regs = [63, 40, 51, 0x24, 30, 0, 25, 28, 3, 18, 32, 8, 0x28, 0, 0, 0];
  const operations = [];
  regs.forEach((value, index) => operations.push([1, 0, index], [1, 1, value]));
  operations.push([2, 0, 2]);
  while (field < 22 && step < 2000000) {
    v.polltime(1);
    const op = operations[step++];
    if (op) (op[0] === 1 ? v.crtc : v.ula).write(op[1], op[2]);
  }
  if (field !== 22 || !pixels.length) throw Error("Incomplete original teletext scanout");
  cases.push({ pattern, steps: step, operations, pixels });
}
const out = fs.openSync(output, "w");
fs.writeSync(out, `cases ${cases.length}\n`);
for (const [id, c] of cases.entries()) {
  fs.writeSync(
    out,
    `case ${id} 3 0 ${c.pattern} ${c.steps} ${c.operations.length} ${c.pixels.length}\n`,
  );
  for (const op of c.operations) fs.writeSync(out, op.join(" ") + "\n");
  for (const row of c.pixels) fs.writeSync(out, row.join(" ") + "\n");
}
fs.closeSync(out);
console.log(
  JSON.stringify({ cases: cases.length, pixels: cases.reduce((n, c) => n + c.pixels.length, 0) }),
);
