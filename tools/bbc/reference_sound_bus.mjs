// Original chip and scheduler; shared declared reset clock, no game phase fitting.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { observeDigitalAudio } from "./reference_audio.mjs";
const [root, input, output, cycles] = process.argv.slice(2);
const { SoundChip } = await import(pathToFileURL(root + "/src/soundchip.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const chip = new SoundChip(() => {}),
  scheduler = new Scheduler(),
  levels = [],
  pins = [],
  writes = [];
chip.setScheduler(scheduler);
chip.reset(true);
for (const byte of [0x9f, 0xbf, 0xdf, 0xff, 0x80]) chip.poke(byte);
observeDigitalAudio(chip, (c) => {
  levels.push(Buffer.from(c.levels));
  pins.push(Buffer.from(c.pins));
});
const poke = chip.poke.bind(chip);
chip.poke = (byte) => {
  writes.push([scheduler.epoch * 16, byte]);
  poke(byte);
};
for (const row of fs.readFileSync(input, "utf8").trim().split("\n")) {
  const [cycle, enabled, touch, data] = row.split(/\s+/).map(Number);
  if (
    !Number.isSafeInteger(cycle / 16) ||
    cycle < 0 ||
    cycle >= Number(cycles) ||
    ![0, 1].includes(enabled) ||
    ![0, 1].includes(touch) ||
    !Number.isInteger(data) ||
    data < 0 ||
    data > 255
  )
    throw Error("Invalid declared sound bus event");
  scheduler.polltime(cycle / 16 - scheduler.epoch);
  chip.updateSlowDataBus(data, !!enabled);
}
scheduler.polltime(Number(cycles) / 16 - scheduler.epoch);
chip.catchUp();
fs.writeFileSync(output, Buffer.concat(levels));
fs.writeFileSync(output + ".pins", Buffer.concat(pins));
fs.writeFileSync(output + ".writes", writes.map((e) => e.join(" ") + "\n").join(""));
