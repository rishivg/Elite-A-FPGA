import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { configureDrive } from "./reference_drive.mjs";
const [root, ssd, output, profile = "clocked-fm"] = process.argv.slice(2);
if (
  !root ||
  !ssd ||
  !output ||
  ![5, 6].includes(process.argv.length) ||
  !["constant-fm", "clocked-fm"].includes(profile)
)
  throw Error("Expected pinned jsbeeb directory, SSD, output and optional FM profile");
const { IntelFdc } = await import(pathToFileURL(path.join(root, "src/intel-fdc.js")));
const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
const scheduler = new Scheduler();
let nmi = false;
const cpu = { setNmi: (source, level) => (nmi = level), nmiAsserted: () => nmi };
const fdc = new IntelFdc(cpu, scheduler);
const disc = discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd)));
fdc.loadDisc(0, disc);
configureDrive(fdc, profile);
const { IbmDiscFormat } = await import(pathToFileURL(path.join(root, "src/disc.js")));
const decode = new Map(
  Array.from({ length: 256 }, (_, d) => [IbmDiscFormat.fmTo2usPulses(255, d) >>> 0, d]),
);
const flux = [],
  oldFlux = fdc._drives[0].writePulses.bind(fdc._drives[0]);
fdc._drives[0].writePulses = (p) => {
  flux.push([scheduler.epoch, fdc._drives[0].headPosition, decode.get(p >>> 0) ?? -1]);
  return oldFlux(p);
};
const bus = [];
function write(a, d) {
  bus.push([scheduler.epoch, "W", a, d, +nmi]);
  fdc.write(a, d);
}
function read(a) {
  const level = +nmi,
    d = fdc.read(a);
  bus.push([scheduler.epoch, "R", a, d, level]);
  scheduler.polltime(2);
  return d;
}
function command(c, ...p) {
  write(0, c);
  scheduler.polltime(10);
  for (const v of p) {
    write(1, v);
    scheduler.polltime(10);
  }
}
command(0x35, 0x0d, 12, 10, 0xc8);
command(0x35, 0x10, 255, 255, 0);
command(0x35, 0x18, 255, 255, 0);
command(0x3a, 0x17, 0xc1);
command(0x3a, 0x23, 0x48);
scheduler.polltime(850000);

command(0x6c);
read(1);
const cases = [];
for (const [track, sector, count] of [
  [0, 0, 1],
  [0, 1, 1],
  [2, 3, 1],
  [2, 4, 2],
  [0, 0, 1],
]) {
  const baseline = scheduler.epoch;
  command(0x53, track, sector, 0x20 | count);
  let first = null,
    last = null,
    values = [],
    times = [],
    completion = null;
  while (scheduler.epoch - baseline < 10000000) {
    scheduler.polltime(1);
    if (nmi) {
      const status = read(0);
      if (status & 4) {
        first ??= scheduler.epoch - baseline;
        last = scheduler.epoch - baseline;
        values.push(read(4));
        times.push(scheduler.epoch - baseline);
      } else {
        completion = { cycle: scheduler.epoch - baseline, result: read(1) };
        break;
      }
    }
  }
  if (completion?.result !== 0 || values.length !== count * 256)
    throw Error("Independent read failed");
  cases.push({
    track,
    sector,
    count,
    first,
    last,
    completion,
    bytes: values.length,
    intervals: times.slice(1).map((t, i) => t - times[i]),
    data: values,
  });
}

// Requests for WRITE precede serial data emission; qualify the actual original
// host handshake rather than assuming READ and WRITE have the same phase.
command(0x4b, 3, 2, 0x21);
let written = 0,
  writeResult = null;
while (writeResult === null) {
  scheduler.polltime(1);
  if (nmi) {
    if (read(0) & 4) {
      if (written >= 256) throw Error("Extra WRITE request");
      write(4, (written * 17 + 29) & 255);
      written++;
      scheduler.polltime(2);
    } else writeResult = read(1);
  }
}
if (written !== 256 || writeResult !== 0) throw Error("Original WRITE did not complete");
// Read back the completed sector through the original controller.
command(0x53, 3, 2, 0x21);
let readback = 0,
  readbackResult = null;
while (readbackResult === null) {
  scheduler.polltime(1);
  if (nmi) {
    if (read(0) & 4) {
      if (read(4) !== ((readback * 17 + 29) & 255)) throw Error("Written sector readback mismatch");
      readback++;
    } else readbackResult = read(1);
  }
}
if (readback !== 256 || readbackResult !== 0) throw Error("Written sector readback failed");
const absentDrives = [];
for (const select of [0x80]) {
  for (const operation of [0x13, 0x0b]) {
    const baseline = scheduler.epoch;
    command(select | operation, 0, 0, 0x21);
    let completion = null;
    while (scheduler.epoch - baseline < 20000000) {
      scheduler.polltime(2);
      if (nmi) {
        if (read(0) & 4) throw Error("Absent drive requested sector payload");
        completion = read(1);
        break;
      }
    }
    if (completion !== 0x10)
      throw Error(
        "Absent drive " +
          select +
          " op " +
          operation +
          " did not report DriveNotReady: " +
          completion,
      );
    absentDrives.push({ select, operation, result: completion });
  }
}
// Returning to stopped drive 0 clears the board READY latch. Head load alone
// cannot produce two 200 ms index edges during its 64 ms settling interval.
const coldStart = scheduler.epoch;
command(0x53, 0, 0, 0x21);
let coldResult = null;
while (scheduler.epoch - coldStart < 2000000) {
  scheduler.polltime(1);
  if (nmi) {
    if (read(0) & 4) throw Error("Cold drive requested payload");
    coldResult = read(1);
    break;
  }
}
if (coldResult !== 0x10) throw Error("Cold command manufactured READY");
fs.writeFileSync(output + ".flux.json", JSON.stringify(flux));
fs.writeFileSync(output + ".bus.tsv", bus.map((row) => row.join(" ")).join("\n") + "\n");
fs.writeFileSync(
  output,
  JSON.stringify(
    {
      profile,
      fixture: "constant 300 rpm, standard FM SSD track, 2 MHz host; unchanged pinned FDC source",
      cases,
      written,
      readback,
      absentDrives,
      coldResult,
    },
    null,
    2,
  ) + "\n",
);
