// Public counter reads during search, WRITE gap/sync, payload and completion.
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
for (const name of ["read", "write", "verify", "write-edges"]) {
  const operation = name === "write-edges" ? "write" : name;
  const scheduler = new Scheduler();
  let nmi = false;
  const fdc = new IntelFdc(
    { setNmi: (_source, value) => (nmi = value), nmiAsserted: () => nmi },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  configureDrive(fdc, "clocked-fm");
  const rows = [],
    counters = [];
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
  function counter() {
    const hi = read(2),
      lo = read(3);
    counters.push([scheduler.epoch - 4, hi, lo]);
  }
  counter();
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
  command({ read: 0x53, write: 0x4b, verify: 0x5f }[operation], 3, 2, 0x21);
  const deadline = scheduler.epoch + 8000000;
  let result = null,
    bytes = 0;
  while (result === null && scheduler.epoch < deadline) {
    scheduler.polltime(1);
    // The narrow edge fixture brackets the fixed track-3/sector-2 WRITE
    // preamble. Other cases sample throughout the command, every 32 us.
    const observe =
      name === "write-edges"
        ? scheduler.epoch >= 1283050 && scheduler.epoch <= 1285400
        : scheduler.epoch % 64 === 0;
    if (observe) counter();
    if (!nmi) continue;
    if (read(0) & 4) {
      counter();
      if (operation === "write") write(4, (bytes * 19 + 3) & 255);
      else read(4);
      bytes++;
    } else result = read(1);
  }
  if (result !== 0 || bytes !== (operation === "verify" ? 0 : 256))
    throw Error("Original transfer failed");
  counter();
  write(2, 1);
  counter();
  for (const address of [0, 0x13, 0x14]) {
    command(0x3d, address);
    read(1);
  }
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ name, fixture, rows: rows.length, counters });
}
fs.writeFileSync(
  output,
  JSON.stringify({ cases, own_cold_cases: true, universal_game_equivalence: false }) + "\n",
);
