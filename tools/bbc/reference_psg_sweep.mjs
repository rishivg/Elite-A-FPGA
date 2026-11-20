// Observe the unchanged original PSG through public byte writes and clocks.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { observeDigitalAudio } from "./reference_audio.mjs";
const [root, fixturePath, output] = process.argv.slice(2);
const { SoundChip } = await import(pathToFileURL(root + "/src/soundchip.js"));
const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
const chip = new SoundChip(() => {}),
  levels = [],
  pins = [];
const whiteStates = new Set(),
  periodicStates = new Set();
for (const [method, seen] of [
  ["shiftLfsrWhiteNoise", whiteStates],
  ["shiftLfsrPeriodicNoise", periodicStates],
]) {
  const original = chip[method];
  chip[method] = function () {
    original.call(this);
    seen.add(this.lfsr);
  };
}
chip.reset(true);
for (const byte of [0x9f, 0xbf, 0xdf, 0xff, 0x80]) chip.poke(byte);
observeDigitalAudio(chip, (chunk) => {
  if (chunk.sine) throw Error("External sine source in PSG sweep");
  levels.push(Buffer.from(chunk.levels));
  pins.push(Buffer.from(chunk.pins));
});
const periods = [new Set(), new Set(), new Set()];
const linkedPeriods = [new Set(), new Set()];
const noiseControls = new Set();
function advance(count) {
  if (count > 0 && (chip.registers[3] & 3) === 3)
    linkedPeriods[(chip.registers[3] >> 2) & 1].add(chip.registers[2]);
  chip.advance(count);
}
let previous = 0;
for (const [cycle, byte] of fixture.writes) {
  advance((cycle - previous) / 16);
  chip.poke(byte);
  noiseControls.add(chip.registers[3] & 7);
  for (let channel = 0; channel < 3; channel++) periods[channel].add(chip.registers[channel]);
  previous = cycle;
}
advance((fixture.cycles - previous) / 16);
fs.writeFileSync(output, Buffer.concat(levels));
fs.writeFileSync(output + ".pins", Buffer.concat(pins));
fs.writeFileSync(
  output + ".coverage.json",
  JSON.stringify({
    tone_periods: periods.map((p) => p.size),
    linked_periods: linkedPeriods.map((p) => p.size),
    noise_controls: noiseControls.size,
    white_states: whiteStates.size,
    periodic_states: periodicStates.size,
  }),
);
