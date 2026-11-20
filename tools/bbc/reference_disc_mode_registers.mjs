// Public shared-status readbacks for every writable mode value.
// Public internal-register reads after independently cold host prefixes.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const [root, ssd, output] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(path.join(root, "src/intel-fdc.js")));
const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
const cases = [];
for (let mode = 0; mode < 256; mode++) {
  const prefix = "mode",
    address = 0x17;
  const scheduler = new Scheduler();
  let nmi = false;
  const fdc = new IntelFdc(
    { setNmi: (_source, value) => (nmi = value), nmiAsserted: () => nmi },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  const rows = [];
  function write(a, d) {
    rows.push([scheduler.epoch, "W", a, d, +nmi]);
    fdc.write(a, d);
    scheduler.polltime(10);
  }
  function read(a) {
    const level = +nmi,
      value = fdc.read(a);
    rows.push([scheduler.epoch, "R", a, value, level]);
    scheduler.polltime(2);
    return value;
  }
  if (prefix === "mode") {
    write(0, 0x3a);
    write(1, 0x17);
    write(1, mode);
  }
  if (prefix === "data") write(4, 0xa5);
  if (prefix === "ram" || prefix === "noparam") {
    write(0, 0x35);
    write(1, 0x03);
    write(1, 0x9a);
    write(1, 0x65);
    write(1, 0xb4);
  }
  if (prefix === "noparam") {
    write(0, 0x35);
    write(1, 0x07);
    write(1, address);
    write(1, 0x01);
    write(1, 0x02);
    write(0, 0x3c);
  } else {
    write(0, 0x3d);
    write(1, address);
  }
  read(0);
  const value = read(1);
  write(0, 0x3d);
  write(1, address);
  const second = read(1);
  read(0);
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + mode + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ mode, value, second, rows: rows.length, fixture });
}
fs.writeFileSync(output, JSON.stringify({ own_cold_cases: true, cases }) + "\n");
