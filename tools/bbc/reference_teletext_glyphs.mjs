import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Teletext } = await import(pathToFileURL(root + "/src/teletext.js"));
const { makeChars } = await import(pathToFileURL(root + "/src/teletext_data.js"));
const chip = new Teletext(),
  font = makeChars(),
  records = [];
function row(character, raster) {
  if (character < 32 || raster < 0 || raster >= 20) return 0;
  let bits = 0;
  for (let x = 0; x < 6; x++) bits |= font[(character - 32) * 60 + (raster >>> 1) * 6 + x] << x;
  return bits;
}
for (let set = 0; set < 3; set++)
  for (let character = 0; character < 128; character++)
    for (let raster = 0; raster < 32; raster++) {
      const alpha = row(character, raster),
        neighbour = row(character, raster + (raster & 1 ? 1 : -1));
      const input = character | (set << 7) | (raster << 9) | (alpha << 14) | (neighbour << 20);
      const table = [chip.normalGlyphs, chip.graphicsGlyphs, chip.separatedGlyphs][set];
      const expected = character >= 32 && raster < 20 ? table[(character - 32) * 20 + raster] : 0;
      records.push([input, expected >>> 0]);
    }
const bytes = Buffer.alloc(12 + records.length * 8);
bytes.write("BBCGLYP", 0, "ascii");
bytes[7] = 1;
bytes.writeUInt32LE(records.length, 8);
records.forEach((record, i) =>
  record.forEach((word, j) => bytes.writeUInt32LE(word, 12 + i * 8 + j * 4)),
);
fs.writeFileSync(output, bytes);
console.log(JSON.stringify({ observations: records.length }));
