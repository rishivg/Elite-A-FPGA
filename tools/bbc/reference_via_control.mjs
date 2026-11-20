import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const cases = [];
for (let pcr = 0; pcr < 256; pcr++)
  for (let initialCa1 = 0; initialCa1 < 2; initialCa1++)
    for (let initialCb1 = 0; initialCb1 < 2; initialCb1++) {
      const scheduler = new Scheduler();
      const via = new UserVia({ interrupt: 0 }, scheduler, false, {
        write() {},
        read() {
          return 255;
        },
      });
      const records = [];
      let ca1 = 0,
        cb1 = 0,
        events = 0;
      via.ca2changecallback = (level, output) => {
        if (output) events |= 1 << (level ? 12 : 14);
      };
      via.cb2changecallback = (level, output) => {
        if (output) events |= 1 << (level ? 13 : 15);
      };
      function step(
        write = false,
        address = 0,
        data = 0,
        nextCa1 = ca1,
        nextCb1 = cb1,
        cb1Pulse = false,
      ) {
        events = 0;
        scheduler.polltime(1);
        ca1 = nextCa1;
        cb1 = nextCb1;
        via.setca1(!!ca1);
        via.setcb1(!!cb1);
        if (cb1Pulse) {
          via.setcb1(!cb1);
          via.setcb1(!!cb1);
        }
        const tick = !(scheduler.epoch & 1);
        if (write) {
          if (!tick) throw Error("Write off the declared one-MHz endpoint");
          via.write(address, data);
        }
        const pcr = via.read(12);
        const expected =
          pcr |
          (+!!(via.ca2 && pcr & 8) << 8) |
          (+!!(via.cb2 && pcr & 128) << 9) |
          (+!!(pcr & 8) << 10) |
          (+!!(pcr & 128) << 11) |
          events;
        const input =
          +write |
          (address << 1) |
          (data << 5) |
          (ca1 << 13) |
          (cb1 << 14) |
          (+tick << 15) |
          (+cb1Pulse << 16);
        records.push([input, expected]);
      }
      function bus(address, data) {
        if ((scheduler.epoch + 1) & 1) step();
        step(true, address, data);
      }
      bus(12, pcr);
      step(false, 0, 0, initialCa1, initialCb1);
      // Immediate retriggers include a pulse expiry and low-going write on one endpoint.
      for (let i = 0; i < 8; i++) {
        bus(1, (pcr + i) & 255);
        bus(0, (pcr - i) & 255);
        bus(15, 255);
      }
      let seed = ((pcr + 1) * 0x6522) ^ (initialCa1 << 8) ^ initialCb1;
      for (let i = 0; i < 96; i++) {
        seed ^= seed << 13;
        seed ^= seed >>> 17;
        seed ^= seed << 5;
        const tick = !((scheduler.epoch + 1) & 1),
          address = [0, 1, 12, 15][(seed >>> 7) & 3];
        step(
          tick && (seed & 3) !== 0,
          address,
          (seed >>> 16) & 255,
          (seed >>> 3) & 1,
          (seed >>> 4) & 1,
          !!(seed & 32),
        );
      }
      for (let i = 0; i < 4; i++) step();
      cases.push(records);
    }
const fd = fs.openSync(output, "w"),
  header = Buffer.alloc(12);
header.write("VIACTRL", 0, "ascii");
header[7] = 2;
header.writeUInt32LE(cases.length, 8);
fs.writeSync(fd, header);
let observations = 0;
for (let i = 0; i < cases.length; i++) {
  const records = cases[i],
    head = Buffer.alloc(8),
    bytes = Buffer.alloc(records.length * 8);
  head.writeUInt32LE(i);
  head.writeUInt32LE(records.length, 4);
  records.forEach((r, n) => r.forEach((w, j) => bytes.writeUInt32LE(w >>> 0, n * 8 + j * 4)));
  fs.writeSync(fd, head);
  fs.writeSync(fd, bytes);
  observations += records.length;
}
fs.closeSync(fd);
console.log(JSON.stringify({ cases: cases.length, observations }));
