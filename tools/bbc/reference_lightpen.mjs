// Public CRTC/ULA/VIA operations in the unmodified pinned original model.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const cases = 1536,
  perCase = 768;
const fd = fs.openSync(output, "w"),
  header = Buffer.alloc(12);
header.write("BBCLPEN", 0, "ascii");
header[7] = 3;
header.writeUInt32LE(cases, 8);
fs.writeSync(fd, header);
let id = 0;
for (const fast of [0, 1])
  for (const mode of [0, 1, 3])
    for (let pcr = 0; pcr < 256; pcr++) {
      const scheduler = new Scheduler();
      const via = new UserVia({ interrupt: 0 }, scheduler, false, {
        write() {},
        read() {
          return 255;
        },
      });
      const v = new Video(false, new Uint32Array(1024 * 625), () => {});
      v.reset(
        { videoRead: () => 0 },
        {
          setVBlankInt(level) {
            via.setca1(level);
          },
        },
      );
      via.cb2changecallback = v.cb2changed.bind(v);
      const records = [];
      let cb1 = 0;
      function step(op = 0, address = 0, data = 0, nextCb1 = cb1, pulse = false) {
        v.polltime(1);
        scheduler.polltime(1);
        cb1 = nextCb1;
        via.setcb1(!!cb1);
        if (pulse) {
          via.setcb1(!cb1);
          via.setcb1(!!cb1);
        }
        if (op && !(scheduler.epoch & 1)) throw Error("Off the one-MHz bus endpoint");
        if (op === 1) v.crtc.write(address, data);
        if (op === 2) v.ula.write(address, data);
        if (op === 3) via.write(address, data);
        const input = op | (address << 2) | (data << 6) | (cb1 << 14) | (+pulse << 15);
        const lp = (v.regs[16] << 8) | v.regs[17];
        const pins =
          via.read(12) | (+!!(via.cb2 && via.pcr & 128) << 8) | (+!!(via.pcr & 128) << 9);
        records.push([
          input,
          lp,
          pins,
          v.addr,
          v.horizCounter |
            (v.vertCounter << 8) |
            (v.scanlineCounter << 15) |
            (+v.inVSync << 20) |
            (+v.inHSync << 21) |
            (+!!(v.dispEnabled & 1) << 22),
        ]);
      }
      function bus(op, address, data) {
        if (!((scheduler.epoch + 1) & 1)) step();
        step(op, address, data);
      }
      // Program a real sync width before selecting 2 MHz. The original
      // model's zero-width pulse overlaps its midpoint/ending branch and
      // cannot qualify a width-zero startup transition at that rate.
      bus(2, 0, 0);
      bus(1, 0, 3);
      bus(1, 1, 0x24);
      bus(2, 0, fast ? 16 : 0);
      const regs = [
        [0, 1, 63][(pcr >> 2) % 3],
        32,
        45,
        0x24,
        4,
        pcr % 3,
        3,
        2,
        mode,
        [0, 7, 18][pcr % 3],
        0,
        0,
        0x3f,
        0xf8,
      ];
      for (let i = 0; i < regs.length; i++) {
        bus(1, 0, i);
        bus(1, 1, regs[i]);
      }
      bus(3, 12, pcr);
      let seed = ((pcr + 1) * 0x6845) ^ (fast << 16) ^ mode;
      for (let i = 0; i < 512; i++) {
        seed ^= seed << 13;
        seed ^= seed >>> 17;
        seed ^= seed << 5;
        const endpoint = !!((scheduler.epoch + 1) & 1);
        const op = endpoint ? (seed >>> 4) & 3 : 0;
        const address = op === 3 ? [0, 1, 12, 15][(seed >>> 7) & 3] : (seed >>> 7) & 1;
        // This original model allows writes to R16/R17. Keep timing stimuli to
        // writable registers; the independent C-model probe checks read-only semantics.
        const data =
          op === 1 && address === 0 ? [12, 13, 14, 15][(seed >>> 9) & 3] : (seed >>> 16) & 255;
        step(op, address, data, (seed >>> 2) & 1, endpoint && !!(seed & 8));
      }
      while (records.length < perCase) step();
      if (records.length !== perCase) throw Error("Unexpected light-pen case length");
      const caseHeader = Buffer.alloc(8),
        payload = Buffer.alloc(perCase * 20);
      caseHeader.writeUInt32LE(id++, 0);
      caseHeader.writeUInt32LE(perCase, 4);
      records.forEach((r, i) =>
        r.forEach((word, j) => payload.writeUInt32LE(word >>> 0, i * 20 + j * 4)),
      );
      fs.writeSync(fd, caseHeader);
      fs.writeSync(fd, payload);
    }
fs.closeSync(fd);
if (id !== cases) throw Error("Incomplete light-pen corpus");
console.log(JSON.stringify({ cases, observations: cases * perCase }));
