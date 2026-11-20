// Public register operations on the unchanged original VIA. Own snapshots
// preserve low-counter read side effects only for passive observations.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output, scope = "steady"] = process.argv.slice(2);
if (!["steady", "transitions"].includes(scope)) throw Error("Unknown T2 scope");
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const cases = [];
let observations = 0;
for (let low = 0; low < 256; low++)
  for (const high of [0, 1, 255])
    for (const enterAfterLoad of [false, true]) {
      const scheduler = new Scheduler();
      const via = new UserVia({ interrupt: 0 }, scheduler, false, {
        write() {},
        read() {
          return 255;
        },
      });
      const records = [];
      function step(operation = 0, address = 0, data = 0) {
        scheduler.polltime(2);
        let read = 0;
        if (operation === 1) via.write(address, data);
        if (operation === 2) read = via.read(address);
        const top = via.read(9),
          flags = via.read(13);
        const saved = via.snapshotState();
        const bottom = via.read(8);
        via.restoreState(saved);
        records.push([operation, address, data, top * 256 + bottom, flags, read]);
      }
      const write = (a, d) => step(1, a, d);
      write(14, 0xa0);
      write(11, enterAfterLoad ? 0 : 0x10);
      write(8, low);
      write(9, high);
      if (enterAfterLoad) {
        step();
        write(11, 0x10);
      }
      const ticks = (high === 255 ? (low < 4 ? 256 : 3) : high + 3) * (low + 2) + 8;
      for (let tick = 0; tick < ticks; tick++) {
        if (tick % 17 === 5) step(2, 8);
        else if (tick % 31 === 9) write(13, 0x20);
        else step();
      }
      if (scope === "transitions") {
        // Exit before/at/after the low-byte relatch and run the joined counter.
        for (let offset = 0; offset < 4; offset++) {
          write(11, 0);
          for (let i = 0; i < 7; i++) step();
          write(11, 0x10);
          for (let i = 0; i < low + 2 + offset; i++) step();
          write(8, (low + offset + 1) & 255);
        }
      }
      if (scope === "transitions") {
        write(9, 0);
        for (let i = 0; i < 2 * (low + 6); i++) step();
      }
      const buffer = Buffer.alloc(8 + records.length * 8);
      buffer.writeUInt32LE(cases.length, 0);
      buffer.writeUInt32LE(records.length, 4);
      for (let i = 0; i < records.length; i++) {
        const [op, a, d, count, flags, read] = records[i],
          o = 8 + i * 8;
        buffer[o] = op;
        buffer[o + 1] = a;
        buffer[o + 2] = d;
        buffer.writeUInt16LE(count, o + 4);
        buffer[o + 6] = flags;
        buffer[o + 7] = read;
      }
      cases.push(buffer);
      observations += records.length;
    }
const header = Buffer.alloc(12);
header.write("T2S1", 0);
header.writeUInt32LE(cases.length, 4);
header.writeUInt32LE(observations, 8);
fs.writeFileSync(output, Buffer.concat([header, ...cases]));
console.log(JSON.stringify({ cases: cases.length, observations }));
