// Exercise the original 8271's register-space MMIO mirrors using host commands.
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
  scheduler.polltime(10);
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
  for (const value of parameters) write(1, value);
}
let cases = 0;
for (const value of [0, 0x20, 0x55, 0x80]) {
  for (let slot = 0; slot < 8; slot++) {
    for (let written = 0x20 + slot; written < 0x40; written += 8) {
      command(0x3a, written, value);
      for (let observed = 0x20 + slot; observed < 0x40; observed += 8) {
        command(0x3d, observed);
        read(1);
        read(0);
      }
      cases++;
    }
  }
}
fs.writeFileSync(output, rows.map((row) => row.join(" ")).join("\n") + "\n");
fs.writeFileSync(
  output + ".json",
  JSON.stringify({
    cases,
    rows: rows.length,
    own_cold: true,
    register_addresses: 32,
    values: [0, 0x20, 0x55, 0x80],
    aliases_per_register: 4,
  }) + "\n",
);
