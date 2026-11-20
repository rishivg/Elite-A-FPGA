// Binary observations from the unchanged original Hitachi 6845 model.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out, variant] = process.argv.slice(2);
if (variant !== undefined && variant !== "transitions") throw Error("Unknown CRTC fixture variant");
const { Video, VDISPENABLE } = await import(pathToFileURL(root + "/src/video.js"));
const fd = fs.openSync(out, "w"),
  header = Buffer.alloc(12);
header.write("BBC6845", 0, "ascii");
header[7] = 1;
header.writeUInt32LE(162, 8);
fs.writeSync(fd, header);
let id = 0;
const count = 40276,
  payload = Buffer.alloc(count * 12),
  caseHeader = Buffer.alloc(12);
for (const fast of [0, 1])
  for (const mode of [0, 1, 3])
    for (const adjust of [0, 1, 2])
      for (const total of [0, 1, 63])
        for (const raster of [0, 7, 18]) {
          const v = new Video(false, new Uint32Array(1024 * 625), () => {});
          v.reset({ videoRead: () => 0 }, { setVBlankInt() {} });
          v.ula.write(0, fast ? 16 : 0);
          let index = 0;
          const step = (operation, address, data) => {
            v.polltime(1);
            if (operation) v.crtc.write(address, data);
            payload.writeUInt32LE(operation | (address << 8) | (data << 16), index * 12);
            const state =
              v.horizCounter |
              (v.vertCounter << 8) |
              (v.scanlineCounter << 15) |
              (+v.inVSync << 20) |
              (+v.inHSync << 21) |
              (+!!(v.dispEnabled & VDISPENABLE) << 22) |
              (+v.teletext.levelRA0 << 23);
            payload.writeUInt32LE(state, index * 12 + 4);
            payload.writeUInt32LE(v.addr, index * 12 + 8);
            index++;
          };
          for (let i = 0; i < 256; i++) step(0, 0, 0);
          const regs = [total, 32, Math.max(0, total - 4), 0x24, 4, adjust, 3, 2, mode, raster];
          for (let i = 0; i < regs.length; i++) {
            step(1, 0, i);
            step(1, 1, regs[i]);
          }
          // Change live registers without resetting counters or importing
          // device state. Adjacent address/data writes exercise both clock
          // parities; irregular spacing crosses line, raster and field ends.
          let pending = -1;
          for (let i = 0; i < 40000; i++) {
            if (variant === "transitions" && pending >= 0) {
              const values = [
                [0, 1, 3, 7, 31, 63, 127, 255],
                [0, 1, 2, 16, 32, 64, 255],
                [0, 1, 3, 7, 31, 63, 255],
                [0, 1, 0x10, 0x11, 0x24, 0xff],
                [0, 1, 2, 4, 31, 127],
                [0, 1, 2, 15, 31],
                [0, 1, 2, 3, 7, 127],
                [0, 1, 2, 3, 7, 127],
                [0, 1, 2, 3, 0x10, 0x23, 0x33],
                [0, 1, 2, 7, 18, 31],
              ];
              const choices = values[pending];
              const data = choices
                ? choices[(id + Math.floor(i / 97)) % choices.length]
                : (id * 37 + i * 13) & 255;
              step(1, 1, data);
              pending = -1;
            } else if (variant === "transitions" && i >= 1000 && i % 97 === 0) {
              pending = (id + Math.floor(i / 97)) % 16;
              step(1, 0, pending);
            } else step(0, 0, 0);
          }
          if (index !== count) throw Error("Incomplete independent CRTC case");
          caseHeader.writeUInt32LE(id++, 0);
          caseHeader.writeUInt32LE(fast, 4);
          caseHeader.writeUInt32LE(count, 8);
          fs.writeSync(fd, caseHeader);
          fs.writeSync(fd, payload);
        }
fs.closeSync(fd);
console.log(`Original CRTC: ${id} cases; ${id * count} observations`);
