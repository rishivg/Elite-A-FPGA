import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out] = process.argv.slice(2);
const { SysVia } = await import(pathToFileURL(root + "/src/via.js"));
const { SoundChip } = await import(pathToFileURL(root + "/src/soundchip.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const rows = [];
let id = 0;
for (const positive of [0, 1])
  for (const independent of [0, 1])
    for (const port of [1, 15]) {
      const scheduler = new Scheduler(),
        chip = new SoundChip(() => {});
      chip.setScheduler(scheduler);
      chip.reset(true);
      const cpu = { interrupt: 0 };
      const via = new SysVia(cpu, scheduler, {
        video: { setScreenHwScroll() {} },
        soundChip: chip,
        isMaster: false,
        initialLayout: "physical",
      });
      via.keyDownRaw([0, 1]);
      via.recalculatePortBPins();
      via.recalculatePortAPins();
      const commands = [
        [1, 12, positive * 4 + independent * 2],
        [3, 14, 129],
        [5, 3, 255],
        [7, port, 144],
        [9, 13, 127],
        [11, port, 145],
        [13, 13, 127],
        [15, port, 144],
        [17, 13, 127],
        [19, port, 145],
        [21, port, 144],
        [23, port, 145],
        [25, port, 144],
      ];
      commands.forEach((c) => (c[0] += 2));
      commands.unshift([1, 13, 127]);
      rows.push("case " + id++);
      for (const [slot, a, d] of commands) {
        scheduler.polltime(slot - scheduler.epoch);
        via.write(a, d);
        rows.push([slot * 16, a, d, +(cpu.interrupt !== 0), via.read(13)].join(" "));
      }
    }
fs.writeFileSync(out, rows.join("\n") + "\n");
