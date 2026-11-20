// Exact bitmap RGB from the unchanged original video renderer. Cold machines
// execute all fields; no cursor state or pixels are injected into either model.
// Warm cases program the cursor during vertical blanking of the preceding field.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output, mode = "cold"] = process.argv.slice(2);
if (!["cold", "warm"].includes(mode)) throw Error("Invalid cursor execution mode");
const warm = mode === "warm";
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const cases = [];
for (const fast of [0, 1]) {
  for (let width = 0; width < 8; width++)
    for (let skew = 0; skew < 4; skew++) cases.push([fast, width, skew, 0, 7, 0, 7, 6]);
  for (const [start, end] of [
    [0, 0],
    [3, 5],
    [7, 7],
    [7, 1],
    [8, 9],
    [31, 31],
  ])
    for (let blink = 0; blink < 4; blink++)
      for (const field of [6, 9, 17, 33]) cases.push([fast, 7, 0, start, end, blink, 7, field]);
  for (const position of [0, 7, 31, 32, 33, 63, 991])
    for (const skew of [0, 1, 2]) cases.push([fast, 7, skew, 0, 7, 0, position, 6]);
}
if (cases.length !== 298) throw Error("Incomplete cursor cases");
const fd = fs.openSync(output, "w"),
  header = Buffer.alloc(12);
header.write(warm ? "BCURSOR2" : "BCURSOR1");
header.writeUInt32LE(cases.length, 8);
fs.writeSync(fd, header);
for (let id = 0; id < cases.length; id++) {
  const [fast, width, skew, start, end, blink, position, field] = cases[id];
  const fb = new Uint32Array(1024 * 625);
  let paints = 0,
    latest;
  const video = new Video(false, fb, () => {
    paints++;
    latest = new Uint32Array(fb);
  });
  video.reset({ videoRead: (address) => ((address >>> 3) ^ 0xa5) & 255 }, { setVBlankInt() {} });
  const cursor = (0xc00 + position) & 0x3fff;
  const regs = [
    fast ? 127 : 63,
    fast ? 64 : 32,
    fast ? 90 : 45,
    fast ? 40 : 36,
    38,
    0,
    31,
    34,
    warm ? 0 : skew << 6,
    7,
    warm ? 32 : start | (blink << 5),
    warm ? 0 : end,
    12,
    0,
    warm ? 0 : cursor >>> 8,
    warm ? 0 : cursor & 255,
  ];
  for (let r = 0; r < regs.length; r++) {
    video.crtc.write(0, r);
    video.crtc.write(1, regs[r]);
  }
  video.ula.write(0, (fast ? 16 : 0) | 8 | (warm ? 0 : width << 5));
  for (let i = 0; i < 16; i++) video.ula.write(1, (i << 4) | (i & 7));
  if (warm) {
    while (paints < field - 1) video.polltime(1);
    for (const [r, value] of [
      [8, skew << 6],
      [10, start | (blink << 5)],
      [11, end],
      [14, cursor >>> 8],
      [15, cursor & 255],
    ]) {
      video.crtc.write(0, r);
      video.crtc.write(1, value);
    }
    video.ula.write(0, (fast ? 16 : 0) | 8 | (width << 5));
  }
  while (paints < field) video.polltime(1);
  const parameters = Buffer.alloc(36);
  parameters.writeUInt32LE(id, 0);
  for (let p = 0; p < 8; p++) parameters.writeUInt32LE(cases[id][p], 4 + p * 4);
  const pixels = Buffer.alloc(512 * 248);
  for (let y = 0; y < 248; y++)
    for (let x = 0; x < 512; x++) {
      const c = latest[(80 + 2 * y) * 1024 + 264 + x];
      let rgb = 0;
      for (let channel = 0; channel < 3; channel++) {
        const component = (c >>> (channel * 8)) & 255;
        if (component !== 0 && component !== 255) throw Error("Non-bitmap colour");
        if (component) rgb |= 1 << channel;
      }
      pixels[y * 512 + x] = rgb;
    }
  fs.writeSync(fd, parameters);
  fs.writeSync(fd, pixels);
}
fs.closeSync(fd);
console.log(JSON.stringify({ cases: cases.length, pixels: cases.length * 512 * 248 }));
