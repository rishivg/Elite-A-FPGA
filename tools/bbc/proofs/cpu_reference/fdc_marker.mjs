// Cold public ID CRC routing, IRQ, retry and retained-context histories.
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

function deleted(operation) {
  const m = machine(79);
  for (let i = 0; i < 256; i++) {
    while (!(m.fdc.read(0) & 8) && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
    assert.ok(m.fdc.read(0) & 8, "Missing deleted-write byte");
    m.record("W", 4, i ^ 0x55);
  }
  while (m.fdc.read(0) & 128 && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
  assert.equal(m.fdc.read(0) & 128, 0, "Deleted write did not finish");
  m.record("R", 1);
  for (const [a, d] of [
    [0, operation],
    [1, 0],
    [1, 0],
    [1, 0x21],
  ])
    m.record("W", a, d);
  return m;
}
const deadlines = new Map();
for (const kind of ["normal", "deleted"])
  for (const operation of [83, 95]) {
    const b = kind === "normal" ? machine(operation) : deleted(operation);
    while (b.fdc._state !== 9 && b.scheduler.epoch < 3000000) b.scheduler.polltime(1);
    assert.equal(b.fdc._state, 9, "Missing original data marker");
    deadlines.set(kind + operation, b.scheduler.epoch);
  }
for (const kind of ["normal", "deleted"])
  for (const operation of [83, 95])
    for (const commandByte of kind === "normal"
      ? [0x1c, 0x1d, 0x5c, 0x5d]
      : [0x10, 0x11, 0x50, 0x51])
      for (const offset of [-1, 0, 1]) {
        const m = kind === "normal" ? machine(operation) : deleted(operation),
          deadline = deadlines.get(kind + operation);
        m.at(deadline + offset - 2, "W", 0, 0x7a);
        m.record("W", 1, 0x1f);
        m.record("W", 1, commandByte);
        m.at(deadline + 4);
        for (const delta of [127, 128, 129, 255, 256, 257]) m.at(deadline + delta);
        m.record("R", 4);
        m.record("R", 1);
        m.record("W", 2, 1);
        save(m, `${kind}-marker-${operation}-${commandByte}-${offset}`, {
          kind,
          operation,
          command: commandByte,
          offset,
          deadline,
        });
      }
for (const offset of [-1, 0, 1]) {
  const m = deleted(83),
    deadline = deadlines.get("deleted83");
  m.at(deadline + offset - 2, "W", 0, 0x7a);
  m.record("W", 1, 0x16);
  m.record("W", 1, 0x10);
  m.at(deadline + 4, "R", 1);
  m.at(deadline + 129);
  m.record("W", 2, 1);
  save(m, `deleted-result-${offset}`, { kind: "deleted", offset, deadline });
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(directory + "/original.json", JSON.stringify({ passed: true, cases }) + "\n");
