// Public write/readback histories at the end of the declared 80-track medium.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { configureDrive } from "./reference_drive.mjs";

const [root, ssd, output] = process.argv.slice(2);
if (!root || !ssd || !output || process.argv.length !== 5)
  throw Error("Expected pinned jsbeeb, SSD and output");
const { IntelFdc } = await import(pathToFileURL(path.join(root, "src/intel-fdc.js")));
const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
const cases = [];
for (const track of [76, 79]) {
  const scheduler = new Scheduler();
  let nmi = false;
  const fdc = new IntelFdc(
    { setNmi: (_source, value) => (nmi = value), nmiAsserted: () => nmi },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  configureDrive(fdc, "clocked-fm");
  const rows = [];
  function write(address, data) {
    rows.push([scheduler.epoch, "W", address, data, +nmi]);
    fdc.write(address, data);
    scheduler.polltime(2);
  }
  function read(address) {
    const level = +nmi,
      data = fdc.read(address);
    rows.push([scheduler.epoch, "R", address, data, level]);
    scheduler.polltime(2);
    return data;
  }
  function command(opcode, ...parameters) {
    write(0, opcode);
    scheduler.polltime(8);
    for (const parameter of parameters) {
      write(1, parameter);
      scheduler.polltime(8);
    }
  }
  command(0x35, 0x0d, 0, 10, 0xc8);
  command(0x35, 0x10, 255, 255, 0);
  command(0x35, 0x18, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x3a, 0x23, 0x48);
  scheduler.polltime(850000);
  command(0x6c);
  read(1);
  command(0x6c);
  if (!(read(1) & 4)) throw Error("Original medium is not ready");
  let written = 0;
  command(0x4b, track, 9, 0x21);
  let result = null;
  const deadline = scheduler.epoch + 5000000;
  while (result === null && scheduler.epoch < deadline) {
    scheduler.polltime(1);
    if (!nmi) continue;
    if (read(0) & 4) {
      write(4, (written * 19 + 3) & 255);
      written++;
    } else result = read(1);
  }
  if (result !== 0 || written !== 256) throw Error("Original last-sector write failed");
  command(0x53, track, 9, 0x21);
  let readback = 0;
  result = null;
  while (result === null && scheduler.epoch < deadline) {
    scheduler.polltime(1);
    if (!nmi) continue;
    if (read(0) & 4) {
      if (read(4) !== ((readback * 19 + 3) & 255))
        throw Error("Original last-sector readback differs");
      readback++;
    } else result = read(1);
  }
  if (result !== 0 || readback !== 256) throw Error("Original last-sector readback incomplete");
  const fixture = path.basename(output) + `.track${track}.tsv`;
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ track, sector: 9, written, readback, rows: rows.length, fixture });
}
fs.writeFileSync(
  output,
  JSON.stringify({ cases, own_cold_cases: true, universal_game_equivalence: false }) + "\n",
);
