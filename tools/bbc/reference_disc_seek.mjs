// Public seek completion and mechanical register readback; own cold drives.
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
  { name: "direction-current", tracks: [1, 42, 43, 79, 42, 0], bad: [255, 255], mode: 0xc1 },
  { name: "bad-tracks", tracks: [1, 2, 3, 4, 0], bad: [2, 4], mode: 0xc1 },
  { name: "bad-track-wrap", tracks: [254, 255, 0], bad: [254, 255], mode: 0xc1 },
  { name: "single-actuator", tracks: [2, 5, 0], bad: [255, 255], mode: 0xc3 },
  { name: "unloaded-head", tracks: [4, 0, 6], bad: [255, 255], mode: 0xc1, unload: true },
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
  const rows = [],
    observations = [];
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
  function special(address) {
    command(0x7d, address);
    return read(1);
  }
  command(0x35, 0x0d, 0, 10, 0xc8);
  command(0x35, 0x10, ...history.bad, 0);
  command(0x35, 0x18, 255, 255, 0);
  command(0x3a, 0x17, history.mode);
  command(0x3a, 0x23, 0x48);
  scheduler.polltime(850000);
  command(0x6c);
  read(1);
  command(0x6c);
  if (!(read(1) & 4)) throw Error("Original drive is not ready");
  for (const track of history.tracks) {
    if (history.unload) command(0x7a, 0x23, 0x40);
    command(0x69, track);
    const deadline = scheduler.epoch + 5000000;
    while (!nmi && scheduler.epoch < deadline) scheduler.polltime(1);
    if (!nmi || !(read(0) & 8)) throw Error("Original seek did not complete");
    const result = read(1);
    observations.push({
      track,
      result,
      drive_out: special(0x23),
      track0: special(0x12),
      track1: special(0x1a),
    });
    if (history.unload) {
      command(0x53, track, 0, 0x21);
      const transferDeadline = scheduler.epoch + 5000000;
      let bytes = 0,
        result = null;
      while (result === null && scheduler.epoch < transferDeadline) {
        scheduler.polltime(1);
        if (!nmi) continue;
        if (read(0) & 4) {
          read(4);
          bytes++;
        } else result = read(1);
      }
      if (bytes !== 256 || result !== 0) throw Error("Original reloaded-head read failed");
    }
  }
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + history.name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  cases.push({ ...history, observations, fixture, rows: rows.length });
}
fs.writeFileSync(
  output,
  JSON.stringify({ cases, own_cold_cases: true, universal_game_equivalence: false }) + "\n",
);
