// Extract unchanged SAA5050 source font rows; no rendered expectations.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { makeChars } = await import(pathToFileURL(root + "/src/teletext_data.js"));
const chars = makeChars();
const rows = Array.from({ length: 960 }, (_, row) => {
  let value = 0;
  for (let x = 0; x < 6; x++) value |= chars[row * 6 + x] << x;
  return value.toString(16).padStart(2, "0");
});
fs.writeFileSync(output, rows.join("\n") + "\n");
