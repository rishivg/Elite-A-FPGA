// READ ID through public host registers and the unchanged original FM engine.
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
for (const [skipIndex, count] of [
  [0, 1],
  [0, 3],
  [0, 0],
  [1, 3],
]) {
  const scheduler = new Scheduler();
  let nmi = false;
  const fdc = new IntelFdc(
    { setNmi: (_source, value) => (nmi = value), nmiAsserted: () => nmi },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  configureDrive(fdc, "clocked-fm");
  const rows = [],
    ids = [];
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
  command(0x5b, 3, skipIndex, count);
  const deadline = scheduler.epoch + 8000000;
  let result = null;
  while (result === null && scheduler.epoch < deadline) {
    const beforeInterrupt = [scheduler.epoch, "P", 0, fdc.read(0), +nmi];
    scheduler.polltime(1);
    if (!nmi) continue;
    if (beforeInterrupt[0] > rows.at(-1)[0]) rows.push(beforeInterrupt);
    if (read(0) & 4) ids.push(read(4));
    else result = read(1);
  }
  if (result !== 0 || ids.length !== (count || 32) * 4) throw Error("Original READ ID incomplete");
  for (let i = 0; i < ids.length; i += 4)
    if (ids[i] !== 3 || ids[i + 1] !== 0 || ids[i + 2] >= 10 || ids[i + 3] !== 1)
      throw Error("Original standard header differs");
  for (let i = 0; i < 100; i++) read(0);
  const name = `${skipIndex ? "skip-index" : "index"}-${count}`;
  const fixture = path.basename(output) + "." + name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ name, fixture, rows: rows.length, skip_index: skipIndex, count, ids });
}
fs.writeFileSync(
  output,
  JSON.stringify({ cases, own_cold_cases: true, universal_game_equivalence: false }) + "\n",
);
