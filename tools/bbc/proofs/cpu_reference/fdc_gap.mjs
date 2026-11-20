// Public R21 writes and command pin snapshots across live read gaps.
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
  return { scheduler, fdc, rows, record, at, nmi: () => nmi };
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

const baseline = machine(83);
while (baseline.fdc._state !== 11 && baseline.scheduler.epoch < 1500000)
  baseline.scheduler.polltime(1);
assert.equal(baseline.fdc._state, 11, "Missing source gap entry");
const gap = baseline.scheduler.epoch;
while (!(baseline.fdc.read(0) & 8) && baseline.scheduler.epoch < 1500000)
  baseline.scheduler.polltime(1);
assert.ok(baseline.fdc.read(0) & 8, "Missing nominal data request");
const first = baseline.scheduler.epoch;
for (const value of [0, 1, 11, 255])
  for (const offset of [-1, 0, 1, 127, 128, 129]) {
    const witness = machine(83);
    witness.at(gap + offset - 2, "W", 0, 0x7a);
    witness.record("W", 1, 21);
    witness.record("W", 1, value);
    assert.equal(witness.nmi(), false, "Unexpected earlier source IRQ");
    while (!witness.nmi() && witness.scheduler.epoch < 5000000) witness.scheduler.polltime(1);
    assert.ok(witness.nmi(), "Missing source gap outcome");
    const actual = witness.scheduler.epoch;
    const m = machine(83);
    m.at(gap + offset - 2, "W", 0, 0x7a);
    m.record("W", 1, 21);
    m.record("W", 1, value);
    for (const delta of [-1, 0, 1, 127, 128, 129, 1024]) m.at(actual + delta);
    m.record("R", 1);
    m.at(actual + 2048);
    m.record("W", 2, 1);
    save(m, `gap-register-${value}-${offset}`, {
      value,
      offset,
      gap,
      first: actual,
      nominal: first,
    });
  }
for (const value of [16, 28, 52])
  for (const offset of [0, 1, 127, 128, 129]) {
    const witness = machine(83);
    witness.at(gap + offset - 2, "W", 0, 0x7a);
    witness.record("W", 1, 31);
    witness.record("W", 1, value);
    assert.equal(witness.nmi(), false, "Unexpected earlier source IRQ");
    while (!witness.nmi() && witness.scheduler.epoch < 5000000) witness.scheduler.polltime(1);
    assert.ok(witness.nmi(), "Missing source command/gap outcome");
    const actual = witness.scheduler.epoch;
    const m = machine(83);
    m.at(gap + offset - 2, "W", 0, 0x7a);
    m.record("W", 1, 31);
    m.record("W", 1, value);
    for (const delta of [-1, 0, 1, 127, 128, 129, 1024]) m.at(actual + delta);
    m.record("R", 1);
    m.at(actual + 2048);
    m.record("W", 2, 1);
    save(m, `gap-command-${value}-${offset}`, {
      value,
      offset,
      gap,
      first: actual,
      nominal: first,
    });
  }
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(directory + "/original.json", JSON.stringify({ passed: true, cases }) + "\n");
