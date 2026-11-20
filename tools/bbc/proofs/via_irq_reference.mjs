// Exercise public Original VIA restore/read/write APIs without changing their code.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const cpu = { interrupt: 0 };
const via = new UserVia(cpu, new Scheduler(), false, { read: () => 255, write() {} });
const base = via.snapshotState();
let observations = 0;
const restore = (flags, enabled, justhit = 0) =>
  via.restoreState({ ...base, ifr: flags, ier: enabled, justhit });
const check = (flags, enabled) => {
  const irq = !!(flags & enabled);
  if (
    via.read(13) !== (flags | (irq ? 128 : 0)) ||
    via.read(14) !== (enabled | 128) ||
    !!cpu.interrupt !== irq
  )
    throw Error(`Original IRQ/readback mismatch ${flags}/${enabled}`);
  observations++;
};
for (let flags = 0; flags < 128; flags++)
  for (let enabled = 0; enabled < 128; enabled++) {
    restore(flags, enabled);
    check(flags, enabled);
  }
for (let enabled = 0; enabled < 128; enabled++)
  for (let data = 0; data < 256; data++) {
    restore(127, enabled);
    via.write(14, data);
    check(127, data & 128 ? enabled | (data & 127) : enabled & ~data);
  }
for (let flags = 0; flags < 128; flags++)
  for (let data = 0; data < 256; data++)
    for (let justhit = 0; justhit < 4; justhit++)
      for (const enabled of [0, 127]) {
        restore(flags, enabled, justhit);
        via.write(13, data);
        check((flags & ~data) | (justhit & 1 ? 64 : 0) | (justhit & 2 ? 32 : 0), enabled);
      }
fs.writeFileSync(output, JSON.stringify({ passed: true, observations }) + "\n");
