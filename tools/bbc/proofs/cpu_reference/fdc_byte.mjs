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
function save(m, name, metadata) {
  const id = cases.length;
  fs.writeFileSync(directory + `/media-${id}.bin`, finalMedia(m.fdc));
  lines.push(`case ${id} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, ...metadata, rows: m.rows.length });
}
function firstByte(m) {
  while (!(m.fdc.read(0) & 8) && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
  assert.ok(m.fdc.read(0) & 8, "Missing source byte");
  return m.scheduler.epoch;
}
for (const operation of [83, 75, 91])
  for (const mode of [0x80, 0x81, 0x90, 0x91, 0xa0, 0xa1, 0xb0, 0xb1])
    for (const ack of [false, true])
      for (const offset of [-1, 0, 1]) {
        const m = machine(operation),
          first = firstByte(m),
          deadline = first + 128;
        m.record("P", 0, 0, 0);
        if (ack) m.record(operation === 75 ? "W" : "R", 4, 0x55);
        m.at(deadline + offset - 2, "W", 0, 0x7a);
        m.record("W", 1, 0x17);
        m.record("W", 1, mode);
        m.at(deadline + 5);
        m.at(deadline + 129);
        m.record("R", 4);
        m.record("R", 1);
        m.record("W", 2, 1);
        save(m, `mode-${operation}-${mode}-${+ack}-${offset}`, {
          kind: "mode",
          operation,
          mode,
          ack,
          offset,
          first,
          deadline,
        });
      }
for (const operation of [83, 75, 91])
  for (const offset of [-1, 0, 1, 126, 127, 128, 129, 254, 255, 256, 257, 260]) {
    const m = machine(operation),
      first = firstByte(m),
      count = operation === 91 ? 4 : 256;
    for (let n = 0; n < count - 1; n++)
      m.at(first + n * 128, operation === 75 ? "W" : "R", 4, n & 255);
    const last = first + (count - 1) * 128;
    const events = new Map([[last, ["P", 0, 0]]]);
    if (offset < 0) events.set(last + offset, ["P", 0, 0]);
    else events.set(last + offset, [operation === 75 ? "W" : "R", 4, 0x5a]);
    const samples =
      operation === 75
        ? [1, 127, 128, 129, 255, 256, 257, 383, 384, 385, 511, 512, 513, 639, 640, 641, 700]
        : [1, 127, 128, 129, 255, 256, 257, 300];
    for (const delta of samples)
      if (!events.has(last + delta)) events.set(last + delta, ["P", 0, 0]);
    for (const [cycle, args] of [...events].sort((a, b) => a[0] - b[0])) {
      m.at(cycle, ...args);
      if (cycle === last)
        assert.equal(
          m.fdc._irqCallbacks,
          false,
          "Original did not stop byte IRQ callbacks at the final byte",
        );
    }
    m.record("R", 1);
    m.record("R", 2);
    m.record("R", 3);
    m.record("W", 2, 1);
    save(m, `last-${operation}-${offset}`, { kind: "last", operation, offset, first, last, count });
  }
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(directory + "/original.json", JSON.stringify({ passed: true, cases }) + "\n");
