// Public VERIFY and unsupported-DMA command behavior on a Model B.
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
const histories = [
  { name: "verify-one", command: 0x5f, parameters: [3, 2, 0x21], mode: 0xc1, result: 0 },
  { name: "verify-three", command: 0x5f, parameters: [3, 2, 0x23], mode: 0xc1, result: 0 },
  { name: "scan", command: 0x40, parameters: [3, 2, 0x21, 1, 0], mode: 0xc1, result: 10 },
  { name: "scan-deleted", command: 0x44, parameters: [3, 2, 0x21, 1, 0], mode: 0xc1, result: 10 },
  { name: "dma-read", command: 0x53, parameters: [3, 2, 0x21], mode: 0xc0, result: 10 },
  { name: "dma-write", command: 0x4b, parameters: [3, 2, 0x21], mode: 0xc0, result: 10 },
  { name: "dma-id", command: 0x5b, parameters: [3, 1, 1], mode: 0xc0, result: 10 },
];
for (const history of histories) {
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
      value = fdc.read(address);
    rows.push([scheduler.epoch, "R", address, value, level]);
    scheduler.polltime(2);
    return value;
  }
  function command(opcode, ...parameters) {
    write(0, opcode);
    for (const parameter of parameters) write(1, parameter);
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
  if (!(read(1) & 4)) throw Error("Original drive is not ready");
  command(0x69, 3);
  while (!nmi) scheduler.polltime(1);
  if (read(1) !== 0) throw Error("Original initial seek failed");
  command(0x7a, 0x17, history.mode);
  command(history.command, ...history.parameters);
  const deadline = scheduler.epoch + 8000000;
  // Passive status reads do not acknowledge NMI. Record the state on the
  // exact original slot before completion, rather than merely checking an
  // interrupt that might have been held high since an incorrect early finish.
  let beforeInterrupt = null;
  while (!nmi && scheduler.epoch < deadline) {
    beforeInterrupt = [scheduler.epoch, "P", 0, fdc.read(0), +nmi];
    scheduler.polltime(1);
  }
  if (beforeInterrupt && beforeInterrupt[0] > rows.at(-1)[0]) rows.push(beforeInterrupt);
  if (!nmi || read(0) & 4) throw Error("Original command incorrectly requested host data");
  const result = read(1);
  if (result !== history.result) throw Error("Original command result differs");
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + history.name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ ...history, fixture, rows: rows.length, completed_at: scheduler.epoch - 202 });
}
fs.writeFileSync(
  output,
  JSON.stringify({ cases, own_cold_cases: true, universal_game_equivalence: false }) + "\n",
);
