// Exercise jsbeeb's independently maintained BBC SN76489 directly.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const [root, periodString, noiseString, out, durationString] = process.argv.slice(2);
const scripted = periodString === "--script";
const period = scripted ? 0 : Number(periodString),
  noise = scripted ? 0 : Number(noiseString),
  duration = Number(durationString);
const { SoundChip } = await import(pathToFileURL(path.join(root, "src/soundchip.js")));
const chip = new SoundChip(() => {});
const counts = [0, 0, 0, 0],
  toneEdges = [],
  noiseShifts = [],
  registers = [];
const step = chip._doChannelStep.bind(chip);
chip._doChannelStep = (channel, add) => {
  const previous = chip.outputBit[channel],
    result = step(channel, add);
  if (channel < 3 && previous !== chip.outputBit[channel]) {
    toneEdges.push([counts[channel] * 8, channel, Number(chip.outputBit[channel])]);
  }
  counts[channel]++;
  return result;
};
for (const method of ["shiftLfsrWhiteNoise", "shiftLfsrPeriodicNoise"]) {
  const shift = chip[method].bind(chip);
  chip[method] = () => {
    shift();
    noiseShifts.push([(counts[3] - 1) * 8, chip.lfsr]);
  };
}
const writes = [
  0x80 | (period & 15),
  period >> 4,
  0x90,
  0xa0 | (period & 15),
  period >> 4,
  0xbf,
  0xc0 | (period & 15),
  period >> 4,
  0xdf,
  0xe0 | noise,
  0xff,
];
const events = scripted
  ? fs
      .readFileSync(noiseString, "utf8")
      .trim()
      .split("\n")
      .map((x) => x.trim().split(/\s+/).map(Number))
  : writes.map((value, i) => [i * 16, value]);
const attenuation = (channel) => {
  for (let i = 0; i < 16; i++) {
    const expected = i === 15 ? 0 : Math.fround(Math.pow(10, -i / 10) / 4);
    if (Math.abs(chip.volume[channel] - expected) < 1e-7) return i;
  }
  throw new Error("Unknown reference attenuation");
};
let cycle = 0;
for (const [next, value] of events) {
  chip.advance((next - cycle) / 2);
  cycle = next;
  chip.poke(value);
  registers.push([
    cycle,
    ...Array.from(chip.registers).map((x, i) => (i === 3 ? x & 7 : x)),
    ...[0, 1, 2, 3].map(attenuation),
    chip.latchedRegister >> 5,
    (chip.latchedRegister >> 4) & 1,
  ]);
}
chip.advance((duration - cycle) / 2);
// jsbeeb renders one channel at a time per buffer; combine its events in
// timestamp order instead of treating callback order as simultaneous pins.
let bits = 0;
const tones = toneEdges
  .sort((a, b) => a[0] - b[0] || a[1] - b[1])
  .map(([cycle, channel, value]) => {
    bits = (bits & ~(1 << channel)) | (value << channel);
    return [cycle, bits];
  });
fs.writeFileSync(path.join(out, "tones.tsv"), tones.map((x) => x.join("\t")).join("\n") + "\n");
fs.writeFileSync(
  path.join(out, "noise.tsv"),
  noiseShifts.map((x) => x.join("\t")).join("\n") + "\n",
);
fs.writeFileSync(
  path.join(out, "registers.tsv"),
  registers.map((x) => x.join("\t")).join("\n") + "\n",
);
// Quantize the independently maintained, floating-point 2 dB table to the
// DUT's declared 6-bit-per-channel peak. This is not an analog transfer test.
const amplitudes = [];
for (let channel = 0; channel < 4; channel++) {
  const table = [];
  for (let attenuation = 0; attenuation < 16; attenuation++) {
    chip.poke(0x90 | (channel << 5) | attenuation);
    table.push(Math.round(chip.volume[channel] * 252));
  }
  amplitudes.push(table);
}
fs.writeFileSync(path.join(out, "amplitudes.json"), JSON.stringify(amplitudes) + "\n");
fs.writeFileSync(
  path.join(out, "state.json"),
  JSON.stringify(
    {
      period,
      noise,
      duration_core_cycles: duration,
      core_hz: 4000000,
      sample_hz: 500000,
      registers: Array.from(chip.registers),
      lfsr: chip.lfsr,
    },
    null,
    2,
  ) + "\n",
);
