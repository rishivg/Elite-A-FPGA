import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
// Only bus operations and elapsed clocks enter the independent device.
const rows = [];
let cases = 0,
  operations = 0;
const prefix = [
  [11, 0],
  [4, 255],
  [5, 255],
  [8, 255],
  [9, 255],
  [13, 127],
  [14, 224],
];
function history(commands) {
  const scheduler = new Scheduler();
  const cpu = { interrupt: 0 };
  const via = new UserVia(cpu, scheduler, false, {
    read() {
      return 255;
    },
    write() {},
  });
  rows.push(`case ${cases++}`);
  for (const [wait, write, addr, data] of commands) {
    scheduler.polltime(2 * (wait + 1));
    let value = 0;
    if (write) via.write(addr, data);
    else value = via.read(addr);
    rows.push(
      `${wait} ${write ? 1 : 0} ${addr} ${data} ${value} ${via.read(13)} ${cpu.interrupt ? 1 : 0} ${via.read(0) >>> 7}`,
    );
    operations++;
  }
}
let seed = 6522;
const random = () => {
  seed ^= seed << 13;
  seed ^= seed >>> 17;
  seed ^= seed << 5;
  return seed >>> 0;
};
for (let c = 0; c < 128; c++) {
  const commands = prefix.map(([addr, data]) => [0, 1, addr, data]);
  for (let i = 0; i < 512; i++) {
    const addr = [0, 4, 5, 6, 7, 8, 9, 11, 13, 14][random() % 10];
    const write = addr !== 0 && !!(random() & 1);
    let data = random() & 255;
    if (addr === 11) data &= 0xc0;
    if (addr === 5 || addr === 9) data = 0;
    if (addr === 4 || addr === 6 || addr === 8) data &= 15;
    commands.push([random() % 4, write, addr, data]);
  }
  history(commands);
}
// Switch either direction around expiry, with PB7 selected and released.
for (let load = 0; load < 32; load++)
  for (const oldMode of [0, 0x40])
    for (const newMode of [0, 0x40])
      for (const offset of [-1, 0, 1])
        for (const output of [0, 0x80]) {
          const commands = prefix.map(([addr, data]) => [0, 1, addr, data]);
          commands.push(
            [0, 1, 11, oldMode | output],
            [0, 1, 4, load],
            [0, 1, 5, 0],
            [load + 1 + offset, 1, 11, newMode | output],
            [0, 0, 13, 0],
            [0, 1, 13, 0x40],
            [load + 2, 0, 13, 0],
            [0, 0, 0, 0],
            [0, 0, 4, 0],
            [0, 0, 13, 0],
          );
          history(commands);
        }
if (cases !== 896 || operations !== 79488) throw new Error("Incomplete timer histories");
fs.writeFileSync(output, rows.join("\n") + "\n");
console.log(`${cases} timer histories; ${operations} accepted operations`);
