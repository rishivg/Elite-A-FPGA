// Independent timer observations from the unchanged original MOS VIA model.
// Bus writes finish at declared 1 MHz boundaries. The passive mode preserves
// counter-low side effects through private snapshots; bus mode acknowledges.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output, mode] = process.argv.slice(2);
if (mode && mode !== "bus") throw new Error("Unknown VIA observation mode");
const bus = mode === "bus";
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const rows = [];
let id = 0;
for (const timer of [1, 2])
  for (const continuous of [0, 1])
    for (const value of [0, 1, 2, 3, 254, 255, 256, 65535]) {
      const scheduler = new Scheduler(),
        cpu = { interrupt: 0 };
      const via = new UserVia(cpu, scheduler, false, {
        write() {},
        read() {
          return 255;
        },
      });
      const write = (address, data) => {
        scheduler.polltime(2);
        via.write(address, data);
      };
      write(14, 0xe0);
      write(11, 0x80 | (continuous ? 0x40 : 0));
      write(timer === 1 ? 4 : 8, value & 255);
      write(timer === 1 ? 5 : 9, value >>> 8);
      const ticks = value === 65535 ? value + 6 : 2 * (value + 2) + 6;
      rows.push(`case ${id} ${timer} ${continuous} ${value} ${ticks}`);
      for (let tick = 0; tick <= ticks; tick++) {
        if (tick) scheduler.polltime(2);
        if (value === 65535 && tick > 8 && tick < value - 3) continue;
        const high = via.read(timer === 1 ? 5 : 9),
          flags = via.read(13),
          pb7 = via.read(0) >>> 7;
        const saved = via.snapshotState();
        const low = via.read(timer === 1 ? 4 : 8);
        const after = via.read(13);
        if (!bus) via.restoreState(saved);
        rows.push(
          `sample ${id} ${tick} ${low | (high << 8)} ${flags} ${pb7}${bus ? " " + after : ""}`,
        );
      }
      id++;
    }
fs.writeFileSync(output, rows.join("\n") + "\n");
console.log(`Original VIA: ${id} timer cases; ${rows.length - id} boundary observations`);
