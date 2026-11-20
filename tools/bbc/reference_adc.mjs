// Original ADC and VIA observe a shared bus clock and declared analogue pins.
import fs from "node:fs";
import { resultAdcClass } from "./reference_adc_results.mjs";
import { pathToFileURL } from "node:url";
const [root, out] = process.argv.slice(2);
const { Adc: OriginalAdc } = await import(pathToFileURL(root + "/src/adc.js"));
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const Adc = resultAdcClass(OriginalAdc);
const rows = [];
let cases = 0,
  observations = 0;
// Distinct aligned values avoid the original ADC's status-MSB/channel bug.
// Its completion code ORs bits 11:10 into channel bits instead of placing
// the result MSBs in status bits 5:4. Other patterns need a physical oracle.
const values = [0x0000, 0x1110, 0x2220, 0x3330];
for (const rising of [0, 1])
  for (const long of [0, 1])
    for (let channel = 0; channel < 4; channel++)
      for (const restart of [0, 1]) {
        const scheduler = new Scheduler(),
          cpu = { interrupt: 0 };
        const via = new UserVia(cpu, scheduler, false, {
          write() {},
          read() {
            return 255;
          },
        });
        const adc = new Adc(via, scheduler);
        for (let i = 0; i < 4; i++)
          adc.setChannelSource(i, { getValue: () => values[i], dispose() {} });
        const period = long ? 20000 : 8000,
          again = 5 + period / 2;
        const deadline = restart ? again + (long ? 8000 : 20000) : 5 + period;
        const writes = new Map([
          [1, [1, 14, 0x90]],
          [3, [1, 12, rising ? 16 : 0]],
          [5, [2, 0, channel | (long ? 8 : 0)]],
        ]);
        if (restart) writes.set(again, [2, 0, ((channel + 1) & 3) | (long ? 0 : 8)]);
        // Read ORB on the next slot to acknowledge the completion interrupt.
        writes.set(deadline + 1, [3, 0, 0]);
        writes.set(deadline + 2, [4, 1, 0]);
        const times = [
          ...new Set([
            0,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            again - 1,
            again,
            again + 1,
            5 + period - 1,
            5 + period,
            5 + period + 1,
            deadline - 1,
            deadline,
            deadline + 1,
            deadline + 2,
            deadline + 3,
          ]),
        ].sort((a, b) => a - b);
        rows.push(`case ${cases++} ${times.length}`);
        let current = 0;
        for (const tick of times) {
          while (current < tick) {
            scheduler.polltime(1);
            current++;
            const op = writes.get(current);
            if (op) {
              if (op[0] === 1) via.write(op[1], op[2]);
              else if (op[0] === 2) adc.write(op[1], op[2]);
              else if (op[0] === 3) via.read(op[1]);
              else adc.read(op[1]);
            }
          }
          const op = writes.get(tick) ?? [0, 0, 0];
          rows.push(
            [
              tick,
              ...op,
              adc.status,
              adc.high,
              adc.low,
              via.read(13),
              +!!cpu.interrupt,
              +via.cb1,
            ].join(" "),
          );
          observations++;
        }
      }
// Restart around the completion deadline, including complete-then-start
// on the same bus edge. Acknowledge the initial edge before the collision.
for (const rising of [0, 1])
  for (const long of [0, 1])
    for (const channel of [0, 1, 2, 3])
      for (const delta of [-4, -2, 0, 2, 4]) {
        const scheduler = new Scheduler(),
          cpu = { interrupt: 0 };
        const via = new UserVia(cpu, scheduler, false, {
          write() {},
          read() {
            return 255;
          },
        });
        const adc = new Adc(via, scheduler);
        values.forEach((value, i) =>
          adc.setChannelSource(i, { getValue: () => value, dispose() {} }),
        );
        const period = long ? 20000 : 8000,
          again = 5 + period + delta,
          deadline = again + (long ? 8000 : 20000);
        const times = [
          ...new Set([
            0,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            again - 2,
            again,
            again + 1,
            again + 2,
            5 + period - 1,
            5 + period,
            5 + period + 1,
            deadline - 1,
            deadline,
            deadline + 1,
            deadline + 2,
          ]),
        ].sort((a, b) => a - b);
        const writes = new Map([
          [1, [1, 14, 0x90]],
          [3, [1, 12, rising ? 16 : 0]],
          [5, [2, 0, channel | (long ? 8 : 0)]],
          [7, [3, 0, 0]],
          [again, [2, 0, ((channel + 1) & 3) | (long ? 0 : 8)]],
          [again + 2, [3, 0, 0]],
          [deadline + 2, [4, 1, 0]],
        ]);
        rows.push(`case ${cases++} ${times.length}`);
        let current = 0;
        for (const tick of times) {
          while (current < tick) {
            scheduler.polltime(1);
            current++;
            const op = writes.get(current);
            if (op) {
              if (op[0] === 1) via.write(op[1], op[2]);
              else if (op[0] === 2) adc.write(op[1], op[2]);
              else if (op[0] === 3) via.read(op[1]);
              else adc.read(op[1]);
            }
          }
          const op = writes.get(tick) ?? [0, 0, 0];
          rows.push(
            [
              tick,
              ...op,
              adc.status,
              adc.high,
              adc.low,
              via.read(13),
              +!!cpu.interrupt,
              +via.cb1,
            ].join(" "),
          );
          observations++;
        }
      }

fs.writeFileSync(out, rows.join("\n") + "\n");
console.log(`Original ADC/VIA: ${cases} cases; ${observations} observations`);
