// Observe original keyboard IRQ changes through public GPIO and key APIs.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out] = process.argv.slice(2);
const { SysVia } = await import(pathToFileURL(root + "/src/via.js"));
const { SoundChip } = await import(pathToFileURL(root + "/src/soundchip.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const rows = [];
let id = 0;
for (const positive of [0, 1])
  for (const auto of [0, 1])
    for (let column = 0; column < 10; column++)
      for (let row = 1; row < 8; row++)
        for (let phase = 0; phase < 32; phase++) {
          const scheduler = new Scheduler(),
            chip = new SoundChip(() => {}),
            cpu = { interrupt: 0 };
          chip.setScheduler(scheduler);
          chip.reset(true);
          const via = new SysVia(cpu, scheduler, {
            video: { setScreenHwScroll() {} },
            soundChip: chip,
            isMaster: false,
            initialLayout: "physical",
          });
          if (!positive) via.keyDownRaw([column, row]);
          via.recalculatePortBPins();
          via.recalculatePortAPins();
          const commands = [
            [1, 13, 127],
            [3, 12, positive * 4],
            [5, 3, 255],
            [7, 1, 128 | (row << 4) | column],
            [9, 2, 255],
            [11, 0, auto ? 11 : 3],
            [13, 13, 127],
            [15, 14, 129],
          ];
          for (const [slot, address, data] of commands) {
            scheduler.polltime(slot - scheduler.epoch);
            via.write(address, data);
          }
          scheduler.polltime(32 - scheduler.epoch);
          if (cpu.interrupt) throw Error("Keyboard IRQ fixture did not acknowledge startup");
          if (positive) via.keyDownRaw([column, row]);
          else via.keyUpRaw([column, row]);
          const irq = +(cpu.interrupt !== 0);
          if (irq !== 1) throw Error("Original key edge did not assert its enabled IRQ");
          // The original GPIO API updates IRQ immediately; phase does not
          // enter this calculation. The RTL executes every master edge up to
          // each declared key event and samples IRQ before accepting it.
          rows.push(`case ${id++} ${column} ${row} ${+!positive} ${512 + phase} ${irq}`);
          for (const [slot, address, data] of commands)
            rows.push(`${slot * 16} ${address} ${data}`);
        }
fs.writeFileSync(out, rows.join("\n") + "\n");
console.log(`Original keyboard edges: ${id} cases`);
