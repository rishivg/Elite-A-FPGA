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
for (const mode of [0x80, 0x81, 0x90, 0x91, 0xa0, 0xa1, 0xb0, 0xb1]) {
  const m = machine(91),
    first = firstByte(m);
  m.record("R", 4);
  m.record("W", 0, 0x7a);
  m.record("W", 1, 0x17);
  m.record("W", 1, mode);
  for (let n = 1; n < 4; n++) m.at(first + n * 128 + 1, "R", 4);
  for (const delta of [511, 512, 513, 639, 640, 641, 767, 768, 769, 1024, 2048, 4096])
    m.at(first + delta);
  m.record("R", 1);
  m.record("W", 2, 1);
  save(m, `id-live-${mode}`, { mode, first });
}
for (const command of [0x18, 0xd8])
  for (const offset of [-1, 0, 1]) {
    const m = machine(83);
    while (!(m.fdc._state === 6 && m.fdc._stateCount === 1) && m.scheduler.epoch < 1500000)
      m.scheduler.polltime(1);
    assert.equal(m.fdc._state, 6, "ID CRC not found");
    const deadline = m.scheduler.epoch + 128;
    m.at(deadline + offset - 2, "W", 0, 0x7a);
    m.record("W", 1, 0x1f);
    m.record("W", 1, command);
    m.at(deadline + 5);
    m.at(deadline + 129);
    m.record("R", 1);
    m.record("W", 2, 1);
    save(m, `search-id-command-${command}-${offset}`, { command, offset, deadline });
  }
for (const mode of [0x81, 0xb1]) {
  const m = machine(91),
    first = firstByte(m);
  m.record("R", 4);
  m.record("W", 0, 0x7a);
  m.record("W", 1, 0x17);
  m.record("W", 1, mode);
  for (let n = 1; n < 4; n++) m.at(first + n * 128 + 1, "R", 4);
  m.at(first + 400650, "W", 0, 0x7a);
  m.record("W", 1, 0x17);
  m.record("W", 1, 0xb1);
  assert.equal(m.fdc._state, 6, "Second ID CRC state missing");
  for (const delta of [400767, 400768, 400769, 400895, 400896, 400897]) m.at(first + delta);
  m.record("R", 1);
  m.record("W", 2, 1);
  save(m, `id-retry-irq-${mode}`, { mode, first });
}
for (const mode of [0x81, 0xb1]) {
  const m = machine(91),
    first = firstByte(m);
  m.record("R", 4);
  m.record("W", 0, 0x7a);
  m.record("W", 1, 0x17);
  m.record("W", 1, mode);
  for (let n = 1; n < 4; n++) m.at(first + n * 128 + 1, "R", 4);
  for (const delta of [639, 640, 641, 767, 768, 769]) m.at(first + delta);
  for (let delta = 1024; delta <= 1200000; delta += 1024) m.at(first + delta);
  m.record("R", 1);
  m.record("W", 2, 1);
  save(m, `id-retries-${mode}`, { mode, first });
}
const m = machine(75);
m.at(1199990, "R", 1);
m.record("W", 0, 0x5b);
for (const parameter of [0, 1, 0x21]) m.record("W", 1, parameter);
const first = firstByte(m);
assert.equal(m.fdc._callContext, 5, "Write call context was not retained");
for (let n = 0; n < 4; n++) m.at(first + n * 128 + 1, "R", 4);
assert.equal(m.fdc._regs[10], 0, "Expected sector-zero ID");
const deadline = first + 640;
m.at(deadline - 10, "W", 0, 0x7a);
m.record("W", 1, 7);
m.record("W", 1, 0);
m.at(deadline - 1);
m.at(deadline);
m.at(deadline + 1);
m.at(deadline + 2, "R", 2);
m.at(deadline + 3, "R", 3);
for (let n = 1; n <= 20; n++) m.at(deadline + n * 128);
m.record("R", 1);
m.record("W", 2, 1);
save(m, "retained-write-context", { first, deadline });
for (const mode of [0x80, 0x81, 0x90, 0x91, 0xa0, 0xa1, 0xb0, 0xb1])
  for (const ack of [false, true])
    for (const offset of [-1, 0, 1]) {
      const m = machine(91),
        first = firstByte(m),
        deadline = first + 512;
      for (let n = 0; n < 4; n++) m.at(first + n * 128 + 1, "R", 4);
      m.at(deadline + offset - 2, "W", 0, 0x7a);
      m.record("W", 1, 0x17);
      m.record("W", 1, mode);
      m.at(deadline + 3);
      if (ack) m.at(deadline + 4, "R", 4);
      for (const delta of [127, 128, 129, 255, 256, 257]) m.at(deadline + delta);
      m.record("R", 1);
      m.record("W", 2, 1);
      save(m, `crc-irq-${mode}-${+ack}-${offset}`, { mode, ack, offset, first, deadline });
    }
for (const mode of [0x80, 0x81, 0x90, 0x91, 0xa0, 0xa1, 0xb0, 0xb1])
  for (const ack of [false, true]) {
    const m = machine(83);
    while (!(m.fdc._state === 5 && m.fdc._regs[8] === 11) && m.scheduler.epoch < 1500000)
      m.scheduler.polltime(1);
    assert.equal(m.fdc._state, 5, "ID body not found");
    const first = m.scheduler.epoch,
      deadline = first + 128;
    m.at(deadline - 3, "W", 0, 0x7a);
    m.record("W", 1, 0x17);
    m.record("W", 1, mode);
    for (let n = 0; n < 3; n++) {
      m.at(deadline + n * 128);
      if (ack) m.at(deadline + n * 128 + 1, "R", 4);
    }
    m.at(first + 641);
    m.record("W", 0, 0x7d);
    m.record("W", 1, 0x0a);
    m.record("R", 1);
    m.record("W", 2, 1);
    save(m, `header-irq-${mode}-${+ack}`, { mode, ack, first, deadline });
  }
for (const mode of [0x40, 0x41, 0x90, 0x91, 0xa0, 0xa1]) {
  const m = machine(83);
  while (!(m.fdc._state === 5 && m.fdc._regs[8] === 11) && m.scheduler.epoch < 1500000)
    m.scheduler.polltime(1);
  assert.equal(m.fdc._state, 5, "ID body not found");
  const first = m.scheduler.epoch,
    deadline = first + 128;
  m.at(deadline - 3, "W", 0, 0x7a);
  m.record("W", 1, 0x17);
  m.record("W", 1, mode);
  m.at(first + 641);
  m.record("W", 0, 0x7d);
  m.record("W", 1, 0x17);
  m.record("R", 1);
  m.record("W", 2, 1);
  save(m, `header-stop-${mode}`, { mode, first, deadline });
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(directory + "/original.json", JSON.stringify({ passed: true, cases }) + "\n");
