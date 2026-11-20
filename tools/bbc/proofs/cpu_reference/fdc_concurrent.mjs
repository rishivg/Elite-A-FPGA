// Public overlapping byte/mechanical commands, including the complete final medium.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { IbmDiscFormat } = await import(pathToFileURL(root + "/src/disc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));

function machine(operation) {
  const scheduler = new Scheduler();
  let nmi = false,
    edges = 0;
  const fdc = new IntelFdc(
    {
      setNmi(_source, level) {
        if (level && !nmi) edges++;
        nmi = level;
      },
      nmiAsserted: () => nmi,
    },
    scheduler,
  );
  fdc._log = () => {};
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(discPath))));
  configureDrive(fdc, "clocked-fm");
  scheduler.polltime(16);
  const rows = [];
  function record(action, address, data = 0, advance = 1) {
    assert.ok(Number.isInteger(advance) && advance >= 0);
    scheduler.polltime(advance);
    const returned = action === "W" ? (fdc.write(address, data), data) : fdc.read(address);
    rows.push([scheduler.epoch, action, address, returned, fdc.read(0), +nmi, edges].join(" "));
    return returned;
  }
  const command = (op, ...parameters) => {
    record("W", 0, op);
    for (const parameter of parameters) record("W", 1, parameter);
  };
  const at = (cycle, action = "P", address = 0, data = 0) =>
    record(action, address, data, cycle - scheduler.epoch);
  command(0x35, 0x0d, 1, 0, 0xf0);
  command(0x35, 0x10, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x7a, 0x23, 0x48);
  record("P", 0, 0, 850000);
  command(0x6c);
  record("R", 1);
  command(0x6c);
  assert.ok(record("R", 1) & 4, "Original drive not ready");
  command(operation, 0, 0, 0x21);
  return { scheduler, fdc, rows, record, at };
}

function finalMedia(fdc) {
  const bytes = Buffer.alloc(80 * 3125 * 2);
  for (let track = 0; track < 80; track++) {
    const surface = fdc._drives[0].disc.getTrack(false, track);
    assert.equal(surface.length, 3125);
    for (let cell = 0; cell < 3125; cell++) {
      const { clocks, data, iffyPulses } = IbmDiscFormat._2usPulsesToFm(surface.pulses2Us[cell]);
      assert.equal(iffyPulses, false, "Original write escaped the declared FM word transport");
      bytes.writeUInt16LE((clocks << 8) | data, 2 * (track * 3125 + cell));
    }
  }
  return bytes;
}

const lines = [],
  cases = [];
for (const old of [0x53, 0x4b, 0x5b])
  for (const next of [0x69, 0x53, 0x4b, 0x5b])
    for (const track of [0, 3])
      for (const sector of next === 0x69 ? [0] : [0, 1])
        for (const ack of [false, true])
          for (const offset of [-1, 0, 1]) {
            const m = machine(old);
            while (!(m.fdc.read(0) & 8) && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
            assert.ok(m.fdc.read(0) & 8, "Missing original first byte");
            const first = m.scheduler.epoch,
              deadline = first + 128;
            m.record("P", 0, 0, 0);
            if (ack) m.record("R", 4);
            const parameters = next === 0x69 ? [track] : [track, sector, 0x21];
            m.at(deadline + offset - parameters.length, "W", 0, next);
            for (const parameter of parameters) m.record("W", 1, parameter);
            m.at(deadline + 5);
            if (m.fdc.read(0) & 4) m.record("R", 4);
            m.at(deadline + 128);
            m.record("R", 1);
            m.at(deadline + 20000);
            m.record("W", 2, 1);
            const name = `overlap-${old}-${next}-${track}-${sector}-${+ack}-${offset}`;
            fs.writeFileSync(directory + `/media-${cases.length}.bin`, finalMedia(m.fdc));
            lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
            cases.push({
              name,
              old,
              next,
              track,
              sector,
              ack,
              offset,
              first,
              deadline,
              rows: m.rows.length,
            });
          }
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(directory + "/original.json", JSON.stringify({ passed: true, cases }) + "\n");
