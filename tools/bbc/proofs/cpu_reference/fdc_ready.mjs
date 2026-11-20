// Public latched READY writes before and across sector completion.
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

for (const kind of ["early", "timed"])
  for (const value of [0, 0x81, 4, 0x44])
    for (const offset of kind === "early" ? [-1] : [-1, 0, 1]) {
      const m = machine(83);
      let last = 0;
      for (let i = 0; i < 256; i++) {
        while (!(m.fdc.read(0) & 8) && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
        assert.ok(m.fdc.read(0) & 8, "Read byte missing");
        last = m.scheduler.epoch;
        m.record("R", 4);
      }
      assert.equal(m.fdc._state, 10, "Data CRC was not entered");
      const deadline = last + 256;
      if (kind === "timed") m.at(deadline + offset - 2, "W", 0, 0x7a);
      else m.record("W", 0, 0x7a);
      m.record("W", 1, 27);
      m.record("W", 1, value);
      if (kind === "early") m.at(deadline - 1);
      m.at(deadline + (kind === "early" ? 1 : 2), "R", 1);
      assert.equal(
        m.rows.at(-1).split(" ")[3],
        String((kind === "early" || offset < 0) && !(value & 4) ? 16 : 0),
        "Latched READY did not gate completion",
      );
      m.at(deadline + 128);
      m.record("W", 2, 1);
      save(m, `completion-ready-${value}` + (kind === "timed" ? `-${offset}` : ""), {
        kind,
        value,
        deadline,
        ...(kind === "timed" ? { offset } : {}),
      });
    }
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(directory + "/original.json", JSON.stringify({ passed: true, cases }) + "\n");
