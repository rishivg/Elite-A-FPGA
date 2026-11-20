// Public host histories following an interrupted seek; no internal state edits.
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
  ...[2, 5, 10].flatMap((target) =>
    [20, 47950, 48000, 48050, 100000, 200000].map((elapsed) => [0x40, target, elapsed, target]),
  ),
  ...[2, 5, 10].flatMap((target) => [20, 100000].map((elapsed) => [0x40, target, elapsed, 0])),
  [0x80, 2, 20, 0],
  [0x80, 5, 100000, 0],
  [0x80, 1, 20, 1],
];
for (const [select, target, elapsed, readTrack] of histories) {
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
    for (const value of parameters) write(1, value);
  }
  function prime() {
    command(0x3a, 0x17, 0xc1);
    command(0x3a, 0x23, select | 8);
    scheduler.polltime(850000);
    command(select | 0x2c);
    read(1);
    command(select | 0x2c);
    if (!!(read(1) & 4) !== (select === 0x40)) throw Error("Unexpected original drive READY");
  }
  command(0x35, 0x0d, 12, 10, 0xc8);
  command(0x35, 0x10, 255, 255, 0);
  command(0x35, 0x18, 255, 255, 0);
  prime();
  command(select | 0x29, target);
  scheduler.polltime(elapsed);
  write(2, 1);
  if (read(0) & 0x8c) throw Error("Original seek reset did not abort");
  prime();
  command(select | 0x13, readTrack, 0, 0x21);
  const deadline = scheduler.epoch + 8000000;
  while (!nmi && scheduler.epoch < deadline) scheduler.polltime(1);
  if (!nmi) throw Error("Original recovery command did not finish/request");
  const status = read(0);
  const value = read(status & 4 ? 4 : 1);
  for (let i = 0; i < 100; i++) read(0);
  const name = `${select === 0x80 ? "drive1-" : ""}target${target}-abort${elapsed}${readTrack === 0 ? "-home" : ""}`;
  const fixture = path.basename(output) + "." + name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({
    name,
    fixture,
    rows: rows.length,
    target,
    select,
    elapsed,
    read_track: readTrack,
    first_status: status,
    first_value: value,
  });
}
fs.writeFileSync(
  output,
  JSON.stringify({
    cases,
    rows: cases.reduce((n, c) => n + c.rows, 0),
    own_cold_cases: true,
    universal_game_equivalence: false,
  }) + "\n",
);
