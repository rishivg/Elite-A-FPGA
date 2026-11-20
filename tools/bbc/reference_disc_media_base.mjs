// Independently cold public disc writes, interrupted media and subsequent reads.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { configureDrive } from "./reference_drive.mjs";

export async function createCase(root, ssd) {
  const { IntelFdc } = await import(pathToFileURL(path.join(root, "src/intel-fdc.js")));
  const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
  const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
  const { IbmDiscFormat } = await import(pathToFileURL(path.join(root, "src/disc.js")));
  const decode = new Map(
    Array.from({ length: 256 }, (_, d) => [IbmDiscFormat.fmTo2usPulses(255, d) >>> 0, d]),
  );
  const scheduler = new Scheduler();
  let nmi = false;
  const fdc = new IntelFdc(
    { setNmi: (_source, value) => (nmi = value), nmiAsserted: () => nmi },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  configureDrive(fdc, "clocked-fm");
  const rows = [],
    transfers = [],
    flux = [],
    rawFlux = [];
  const drive = fdc._drives[0],
    writePulses = drive.writePulses.bind(drive);
  drive.writePulses = (p) => {
    flux.push([scheduler.epoch, drive.track, drive.headPosition, decode.get(p >>> 0) ?? -1]);
    const decoded = IbmDiscFormat._2usPulsesToFm(p >>> 0);
    rawFlux.push([scheduler.epoch, drive.track, drive.headPosition, decoded.data, decoded.clocks]);
    return writePulses(p);
  };
  function write(a, d) {
    rows.push([scheduler.epoch, "W", a, d, +nmi]);
    fdc.write(a, d);
    scheduler.polltime(10);
  }
  function read(a, observeNmi) {
    const level = +nmi,
      value = fdc.read(a);
    observeNmi?.(+nmi);
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
  function transfer(op, abort = 0, late = -1, beforeReset = () => {}) {
    command(op, 3, 2, 0x21, ...((op & 0x3c) < 8 ? [1, 0] : []));
    const end = scheduler.epoch + 8000000;
    let bytes = 0,
      result = null;
    const payload = [];
    while (result === null && scheduler.epoch < end) {
      scheduler.polltime(1);
      if (!nmi) continue;
      const status = read(0);
      if (status & 4) {
        if (abort === -1) {
          write(2, 1);
          result = "aborted";
          continue;
        }
        if (bytes === late) {
          scheduler.polltime(2000);
          read(0);
          result = read(1);
          continue;
        }
        if ((op & 0x3c) === 8 || (op & 0x3c) === 12) write(4, (bytes * 19 + 3) & 255);
        else payload.push(read(4));
        bytes++;
        if (abort && bytes === abort) {
          beforeReset();
          write(2, 1);
          result = "aborted";
        }
      } else result = read(1);
    }
    if (result === null) throw Error("Original transfer timed out");
    transfers.push({ op, bytes, result, payload });
    return result;
  }
  return {
    rows,
    transfers,
    flux,
    rawFlux,
    scheduler,
    command,
    transfer,
    ready,
    write,
    read,
    irq: () => nmi,
  };
}

export function emitCase(output, name, test) {
  const { rows, transfers, flux, rawFlux, read } = test;
  for (let i = 0; i < 100; i++) read(0);
  const fixture = path.basename(output) + "." + name + ".tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    rows.map((r) => r.join(" ")).join("\n") + "\n",
  );
  // A late abort can overwrite the just-written FM cell within the same
  // callback and timestamp. The sector-store port represents the final cell.
  const finalCells = new Map();
  for (const [epoch, track, cell, value] of flux)
    if (track === 3 && cell >= 667 && cell <= 922)
      finalCells.set(`${epoch}:${cell}`, [epoch, 8192 + cell - 667, value]);
  const storageWrites = [...finalCells.values()];
  return { name, rows: rows.length, fixture, transfers, storageWrites, flux, rawFlux };
}
