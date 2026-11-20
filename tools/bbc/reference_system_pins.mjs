// Original SysVia/IC32/keyboard and sound scheduler under ordinary bus writes.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out] = process.argv.slice(2);
const { SysVia } = await import(pathToFileURL(root + "/src/via.js"));
const { SoundChip } = await import(pathToFileURL(root + "/src/soundchip.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const commands = [
  [1, 2, 255],
  [3, 0, 8],
  [5, 3, 255],
  [7, 0, 11],
  [9, 15, 144],
  [11, 0, 0],
  [29, 0, 8],
  [41, 0, 0],
  [57, 0, 8],
  [71, 0, 0],
  [75, 15, 132],
  [85, 15, 131],
  [99, 15, 130],
  [113, 0, 8],
  [127, 0, 3],
  [129, 1, 128],
  [131, 0, 0],
  [145, 15, 145],
  [161, 0, 8],
  [175, 3, 127],
  [177, 15, 73],
  [179, 0, 0],
  [195, 0, 8],
  [209, 0, 11],
  [211, 3, 255],
  [213, 15, 144],
  [215, 0, 0],
  [231, 0, 8],
  [243, 3, 15],
  [245, 15, 2],
  [247, 0, 0],
  [263, 0, 8],
  [275, 2, 0],
  [277, 2, 255],
  [279, 1, 255],
  [281, 3, 255],
  [283, 0, 0],
];
const held = [
  [],
  [[0, 0]],
  [[9, 4]],
  [
    [4, 3],
    [8, 5],
  ],
];
const rows = [];
let events = 0;
for (let id = 0; id < held.length; id++) {
  const scheduler = new Scheduler(),
    chip = new SoundChip(() => {});
  chip.setScheduler(scheduler);
  chip.reset(true);
  const via = new SysVia({ interrupt: 0 }, scheduler, {
    video: { setScreenHwScroll() {} },
    soundChip: chip,
    isMaster: false,
    initialLayout: "physical",
  });
  for (const key of held[id]) via.keyDownRaw(key);
  via.recalculatePortBPins();
  via.recalculatePortAPins();
  const writes = [],
    poke = chip.poke.bind(chip);
  chip.poke = (data) => {
    writes.push([scheduler.epoch * 16, data]);
    poke(data);
  };
  const observations = [];
  for (const [slot, address, data] of commands) {
    scheduler.polltime(slot - scheduler.epoch);
    via.write(address, data);
    observations.push([
      slot * 16,
      address,
      data,
      via.portapins,
      via.portbpins,
      via.IC32,
      (via.IC32 >>> 4) & 3,
    ]);
  }
  scheduler.polltime(400 - scheduler.epoch);
  events += writes.length;
  rows.push(`case ${id} ${held[id].length} ${observations.length} ${writes.length}`);
  for (const key of held[id]) rows.push(`key ${key.join(" ")}`);
  for (const row of observations) rows.push(`bus ${row.join(" ")}`);
  for (const row of writes) rows.push(`sound ${row.join(" ")}`);
}
fs.writeFileSync(out, rows.join("\n") + "\n");
console.log(
  `Original system pins: ${held.length} cases; ${commands.length * held.length} bus observations; ${events} sound events`,
);
