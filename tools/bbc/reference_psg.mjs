// Standalone original PSG fixture with an explicitly shared reset clock.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { observeDigitalAudio } from "./reference_audio.mjs";
const [root, fixturePath, output] = process.argv.slice(2);
const { SoundChip } = await import(pathToFileURL(root + "/src/soundchip.js"));
const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
const chip = new SoundChip(() => {}),
  levels = [],
  pins = [];
chip.reset(true);
// Shared muted, zero-period power profile. The original divider's natural
// first transition is at 2 us; no private counter phase is changed.
for (const byte of [0x9f, 0xbf, 0xdf, 0xff, 0x80]) chip.poke(byte);
observeDigitalAudio(chip, (chunk) => {
  levels.push(Buffer.from(chunk.levels));
  pins.push(Buffer.from(chunk.pins));
});
let previous = 0;
for (const [cycle, byte] of fixture.writes) {
  chip.advance((cycle - previous) / 16);
  chip.poke(byte);
  previous = cycle;
}
chip.advance((fixture.cycles - previous) / 16);
fs.writeFileSync(output, Buffer.concat(levels));
fs.writeFileSync(output + ".pins", Buffer.concat(pins));
