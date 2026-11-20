// Observe pixels emitted by the unchanged renderer, including both field parities.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const cases = [];
for (const interlace of [1, 3])
  for (const fast of [0, 1])
    for (const mode of [0, 1, 2, 3]) {
      const fb = new Uint32Array(1024 * 625);
      const v = new Video(false, fb, () => {});
      v.reset(
        {
          videoRead: (address) => {
            // Declared physical-RA memory profile: unchanged beebjit supplies the
            // complete address mapping; jsbeeb omits odd-field RA0 in this RAM path.
            // Correct the electrical memory address without changing CRTC state or
            // the original renderer. The uncorrected counterexample is retained.
            if (interlace === 3 && !(v.addr & 0x2000)) address |= +!v.doEvenFrameLogic;
            return ((address * 73) ^ (address >>> 5) ^ 0xa6) & 255;
          },
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
      const blit = v.blitFb;
      v.blitFb = function (data, offset, width) {
        blit.call(this, data, offset, width);
        if (field < 6 || field > 7) return;
        const linesPerRow = interlace === 3 ? 4 : 8;
        let y =
          this.vertCounter * linesPerRow +
          (interlace === 3 ? this.scanlineCounter / 2 : this.scanlineCounter);
        if (interlace === 3) y = y * 2 + +!this.doEvenFrameLogic;
        for (let x = 0; x < width; x++) {
          const rgb = fb[offset + x];
          pixels.push([
            field,
            this.horizCounter * width + x,
            y,
            rgb & 255,
            (rgb >>> 8) & 255,
            (rgb >>> 16) & 255,
          ]);
        }
      };
      const regs = [
        fast ? 127 : 63,
        fast ? 64 : 32,
        fast ? 90 : 45,
        fast ? 40 : 36,
        38,
        0,
        31,
        34,
        interlace,
        interlace === 3 ? 6 : 7,
        32,
        8,
        12,
        0,
        13,
        130,
      ];
      const operations = [];
      regs.forEach((value, index) => operations.push([1, 0, index], [1, 1, value]));
      operations.push([2, 0, (fast ? 16 : 0) | (mode << 2)]);
      for (let i = 0; i < 16; i++) operations.push([2, 1, (i << 4) | ((i ^ 3) & 7)]);
      while (field < 8 && step < 500000) {
        v.polltime(1);
        const op = operations[step++];
        if (op) (op[0] === 1 ? v.crtc : v.ula).write(op[1], op[2]);
      }
      if (field !== 8 || !pixels.length) throw Error("Incomplete interlace case");
      cases.push({ interlace, fast, mode, steps: step, operations, pixels });
    }
const out = fs.openSync(output, "w");
fs.writeSync(out, `cases ${cases.length}\n`);
for (const [id, c] of cases.entries()) {
  fs.writeSync(
    out,
    `case ${id} ${c.interlace} ${c.fast} ${c.mode} ${c.steps} ${c.operations.length} ${c.pixels.length}\n`,
  );
  for (const op of c.operations) fs.writeSync(out, op.join(" ") + "\n");
  for (const row of c.pixels) fs.writeSync(out, row.join(" ") + "\n");
}
fs.closeSync(out);
console.log(
  JSON.stringify({ cases: cases.length, pixels: cases.reduce((n, c) => n + c.pixels.length, 0) }),
);
