// Query the complete internal bank after a completed public command.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { configureDrive } from "./reference_drive.mjs";
const [root, ssd, output] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(path.join(root, "src/intel-fdc.js")));
const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
const cases = [];
for (const name of ["read", "write", "verify", "id"]) {
  const scheduler = new Scheduler();
  let nmi = false;
  const fdc = new IntelFdc(
    { setNmi: (_source, value) => (nmi = value), nmiAsserted: () => nmi },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  configureDrive(fdc, "clocked-fm");
  const rows = [],
    transfers = [];
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
  function command(op, ...params) {
    write(0, op);
    for (const p of params) write(1, p);
  }
  function ready() {
    command(0x3a, 0x17, 0xc1);
    command(0x3a, 0x23, 0x48);
    scheduler.polltime(850000);
    command(0x6c);
    read(1);
    command(0x6c);
    if (!(read(1) & 4)) throw Error("Drive not ready");
  }
  command(0x35, 0x0d, 0, 10, 0xc8);
  command(0x35, 0x10, 255, 255, 0);
  command(0x35, 0x18, 255, 255, 0);
  ready();
  function transfer(op) {
    command(op, 3, 2, 0x21);
    const end = scheduler.epoch + 8000000;
    let bytes = 0,
      result = null;
    while (result === null && scheduler.epoch < end) {
      scheduler.polltime(1);
      if (!nmi) continue;
      if (read(0) & 4) {
        if (op === 0x4b) write(4, (bytes * 19 + 3) & 255);
        else read(4);
        bytes++;
      } else result = read(1);
    }
    const expected = { read: 256, write: 256, verify: 0, id: 4 }[name];
    if (result !== 0 || bytes !== expected) throw Error("Original transfer failed");
    transfers.push({ op, bytes, result });
  }
  transfer({ read: 0x53, write: 0x4b, verify: 0x5f, id: 0x5b }[name]);
  const registers = [];
  for (let address = 0; address < 32; address++) {
    command(0x3d, address);
    read(0);
    registers.push([address, read(1)]);
    read(0);
  }
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ name, rows: rows.length, fixture, transfers, registers });
}
fs.writeFileSync(output, JSON.stringify({ own_cold_cases: true, cases }) + "\n");
