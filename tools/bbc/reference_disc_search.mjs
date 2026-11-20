// Missing-sector and empty-track searches through original public host APIs.
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
  { name: "sector-ten", track: 3, sector: 10, count: 1, bad: [255, 255], bytes: 0 },
  { name: "sector-sixteen", track: 3, sector: 16, count: 1, bad: [255, 255], bytes: 0 },
  { name: "sector-255", track: 3, sector: 255, count: 1, bad: [255, 255], bytes: 0 },
  { name: "empty-track", track: 80, sector: 0, count: 1, bad: [255, 255], bytes: 0 },
  { name: "past-last-sector", track: 79, sector: 9, count: 2, bad: [255, 255], bytes: 256 },
  { name: "mapped-track", track: 2, sector: 0, count: 1, bad: [2, 4], bytes: 0 },
  { name: "upper-side", track: 3, sector: 0, count: 1, bad: [255, 255], bytes: 0, side: 0x20 },
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
  command(0x35, 0x10, ...history.bad, 0);
  command(0x35, 0x18, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x3a, 0x23, 0x48 | (history.side || 0));
  scheduler.polltime(850000);
  command(0x6c);
  read(1);
  command(0x6c);
  if (!(read(1) & 4)) throw Error("Original drive is not ready");
  command(0x53, history.track, history.sector, 0x20 | history.count);
  const deadline = scheduler.epoch + 8000000;
  let result = null,
    bytes = 0;
  while (result === null && scheduler.epoch < deadline) {
    const beforeInterrupt = [scheduler.epoch, "P", 0, fdc.read(0), +nmi];
    scheduler.polltime(1);
    if (!nmi) continue;
    if (beforeInterrupt[0] > rows.at(-1)[0]) rows.push(beforeInterrupt);
    if (read(0) & 4) {
      read(4);
      bytes++;
    } else result = read(1);
  }
  if (result !== 24 || bytes !== history.bytes) throw Error("Original search result differs");
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + history.name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ ...history, fixture, rows: rows.length });
}
fs.writeFileSync(
  output,
  JSON.stringify({ cases, own_cold_cases: true, universal_game_equivalence: false }) + "\n",
);
