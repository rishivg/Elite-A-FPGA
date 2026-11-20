// Decode independently constructed original disc pulses from shared input SSD bytes.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [root, ssd, output] = process.argv.slice(2);
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
const { IbmDiscFormat } = await import(pathToFileURL(path.join(root, "src/disc.js")));
const disc = discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd)));
const bytes = Buffer.alloc(80 * 3125 * 2);
for (let track = 0; track < 80; track++) {
  const source = disc.getTrack(false, track);
  if (source.length !== 3125) throw Error("Original standard track length changed");
  for (let cell = 0; cell < 3125; cell++) {
    const { clocks, data, iffyPulses } = IbmDiscFormat._2usPulsesToFm(source.pulses2Us[cell]);
    if (iffyPulses) throw Error("Standard input has unexpected off-clock pulses");
    bytes.writeUInt16LE((clocks << 8) | data, (track * 3125 + cell) * 2);
  }
}
fs.writeFileSync(output, bytes);
