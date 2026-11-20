// Reset immediately before, at, and after both emitted CRC cells.
import fs from "node:fs";
import { createCase, emitCase } from "./reference_disc_media_base.mjs";
const [root, ssd, output] = process.argv.slice(2);
const cases = [];
for (const name of [
  "crc1-before",
  "crc1-at",
  "crc1-after",
  "crc2-before",
  "crc2-at",
  "crc2-after",
]) {
  const test = await createCase(root, ssd);
  const { scheduler, flux, transfer, ready } = test;
  transfer(0x4b, 256, -1, () => {
    while (!flux.some(([, track, cell]) => track === 3 && cell === 922)) scheduler.polltime(1);
    const payloadEnd = flux.find(([, track, cell]) => track === 3 && cell === 922)[0];
    const offset = name.endsWith("before") ? -1 : name.endsWith("after") ? 1 : 0;
    scheduler.polltime(
      payloadEnd + (name.startsWith("crc1") ? 128 : 256) + offset - scheduler.epoch,
    );
  });
  ready();
  transfer(0x53);
  cases.push(emitCase(output, name, test));
}
fs.writeFileSync(output, JSON.stringify({ own_cold_cases: true, cases }) + "\n");
