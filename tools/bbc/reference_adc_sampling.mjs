// Observe the declared digital ADC profile while analogue inputs change.
// The unchanged original scheduler/ADC executes every conversion independently.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { resultAdcClass } from "./reference_adc_results.mjs";

const [root, output] = process.argv.slice(2);
const { Adc: OriginalAdc } = await import(pathToFileURL(root + "/src/adc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const Adc = resultAdcClass(OriginalAdc);
const rows = [];
let cases = 0,
  observations = 0;
const initial = [0x123f, 0x456e, 0x89ad, 0xfedc];
const during = [0xaaaa, 0x5555, 0x000f, 0xffff];
const changed = [0xffff, 0x8001, 0x400f, 0x0000];
for (let control = 0; control < 16; control++)
  for (const restartDelta of [null, -2, 0, 2])
    for (const changeDelta of [-4, -2, 0, 2, 4]) {
      const scheduler = new Scheduler();
      let eoc = false,
        values = [...initial];
      const adc = new Adc(
        {
          setcb1(value) {
            eoc = value;
          },
        },
        scheduler,
      );
      values.forEach((_, channel) =>
        adc.setChannelSource(channel, { getValue: () => values[channel], dispose() {} }),
      );
      // Prime a completed result, then retain it while the tested conversion
      // is busy. All accepted control writes use high PHI2 endpoints.
      const start = 8009,
        duration = control & 8 ? 20000 : 8000,
        deadline = start + duration,
        restart = restartDelta === null ? null : deadline + restartDelta,
        nextControl = control ^ 0x0b,
        finalDeadline = restart === null ? deadline : restart + (nextControl & 8 ? 20000 : 8000);
      const writes = new Map([
        [5, 0],
        [start, control],
      ]);
      if (restart !== null) writes.set(restart, nextControl);
      const pinChanges = new Map([
        [start + 2, during],
        [deadline + changeDelta, changed],
      ]);
      const times = new Set([0, 5, 6, 8004, 8005, 8006, start, start + 1, start + 2, start + 3]);
      for (const event of [deadline, finalDeadline, restart, ...pinChanges.keys()]) {
        if (event === null) continue;
        for (const delta of [-2, -1, 0, 1, 2]) times.add(event + delta);
      }
      const ordered = [...times].sort((a, b) => a - b);
      rows.push(`case ${cases++} ${ordered.length}`);
      let current = 0;
      for (const tick of ordered) {
        while (current < tick) {
          current++;
          // Input pins settle before this scheduler endpoint; a conversion
          // completing here observes these values, then a bus write may restart.
          if (pinChanges.has(current)) values = [...pinChanges.get(current)];
          scheduler.polltime(1);
          if (writes.has(current)) adc.write(0, writes.get(current));
        }
        const packed = values.reduce(
          (result, value, channel) => result | (BigInt(value) << BigInt(channel * 16)),
          0n,
        );
        rows.push(
          [
            tick,
            +writes.has(tick),
            writes.get(tick) ?? 0,
            packed.toString(16),
            adc.status,
            adc.high,
            adc.low,
            +eoc,
          ].join(" "),
        );
        observations++;
      }
    }
rows.push(`total ${cases} ${observations}`);
fs.writeFileSync(output, rows.join("\n") + "\n");
console.log(`ADC sampling: ${cases} cases; ${observations} observations`);
