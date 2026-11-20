// Public timer/index ties; original callbacks and scheduler remain unchanged.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
function machine(requestedSector = 10) {
  const scheduler = new Scheduler();
  let nmi = false,
    edges = 0;
  const fdc = new IntelFdc(
    {
      setNmi(_s, level) {
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
  const rows = [],
    events = [];
  for (const [name, label] of [
    ["_timerFired", "timer"],
    ["_checkIndexPulse", "index"],
  ]) {
    const original = fdc[name];
    fdc[name] = function (...args) {
      if (label === "timer" || (this._index && !this._stateIsIndexPulse))
        events.push([scheduler.epoch, label]);
      return original.apply(this, args);
    };
  }
  function record(action, address, data = 0, advance = 1) {
    assert.ok(Number.isInteger(advance) && advance >= 0);
    scheduler.polltime(advance);
    const returned = action === "W" ? (fdc.write(address, data), data) : fdc.read(address);
    rows.push([scheduler.epoch, action, address, returned, fdc.read(0), +nmi, edges].join(" "));
    return returned;
  }
  const command = (op, ...args) => {
    record("W", 0, op);
    for (const arg of args) record("W", 1, arg);
    record("R", 0);
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
  assert.ok(record("R", 1) & 4);
  command(0x53, 0, requestedSector, 0x21);
  return {
    scheduler,
    fdc,
    rows,
    record,
    command,
    at,
    events,
    get nmi() {
      return nmi;
    },
    get edges() {
      return edges;
    },
  };
}
const control = machine();
while (control.fdc._regs[0x1c] === 3 && control.scheduler.epoch < 1500000)
  control.scheduler.polltime(1);
assert.equal(control.fdc._regs[0x1c], 2);
const deadline = control.scheduler.epoch,
  lines = [],
  cases = [];
for (const op of [0x69, 0x53, 0x5b])
  for (const head of [0, 0x10, 0x20, 0xf0])
    for (const offset of [-1, 0, 1]) {
      const m = machine();
      m.at(deadline - 6000, "W", 0, 0x7a);
      m.record("W", 1, 0x0f);
      m.record("W", 1, head);
      m.at(deadline - 5000, "W", 0, 0x7a);
      m.record("W", 1, 0x1c);
      m.record("W", 1, 1);
      const args = op === 0x69 ? [1] : [1, 0, 0x21];
      m.at(deadline - 4000 - args.length + offset, "W", 0, op);
      for (const param of args) m.record("W", 1, param);
      m.record("R", 0);
      m.at(deadline - 1);
      m.at(deadline);
      m.at(deadline + 3);
      const result = m.record("R", 1);
      m.command(0x7d, 0x1c);
      const count = m.record("R", 1);
      m.record("W", 2, 1);
      const name = `${op}-${head}-${offset}`;
      lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
      cases.push({
        name,
        tie: true,
        deadline,
        result,
        count,
        rows: m.rows.length,
        events: m.events.filter(([epoch]) => Math.abs(epoch - deadline) <= 1),
      });
    }
for (let address = 0; address < 64; address++) {
  const m = machine();
  m.command(0x7d, address);
  const returned = m.record("R", 1);
  m.record("W", 2, 1);
  const name = `special-${address}`;
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, tie: false, address, returned, deadline, rows: m.rows.length });
}
{
  const m = machine();
  m.command(0x7a, 0x24, 0x55);
  m.command(0x7d, 0x24);
  const returned = m.record("R", 1);
  assert.equal(returned, 0x40);
  m.record("W", 2, 1);
  const name = "special-clock-temporary";
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, tie: false, returned, deadline, rows: m.rows.length });
}
{
  const m = machine(0);
  while (!(m.fdc.read(0) & 8) && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
  const first = m.scheduler.epoch;
  assert.equal(m.edges, 1);
  m.record("P", 0, 0, 0);
  m.record("R", 4);
  assert.equal(m.nmi, false);
  // The next accepted slot acknowledges data, rather than reading status.
  m.record("W", 0, 0x7a);
  m.record("W", 1, 0x23);
  m.record("W", 1, 0x48);
  const rephase = m.scheduler.epoch;
  m.record("R", 4);
  const next = m.scheduler.epoch;
  assert.equal(next, rephase + 1);
  assert.equal(m.edges, 2);
  assert.equal(m.nmi, false);
  m.record("P", 0, 0, 3);
  m.record("W", 2, 1);
  const name = "rephased-byte-ack";
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({
    name,
    tie: false,
    first,
    rephase,
    next,
    edges: m.edges,
    deadline,
    rows: m.rows.length,
  });
}
// Finite source timing projection; this object is never a DUT checkpoint.
const stepRates = [];
for (let rate = 0; rate < 256; rate++) {
  control.fdc._regs[0x0d] = rate;
  stepRates.push(control.fdc._stepRateMs());
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({ passed: true, deadline, cases, step_rates: stepRates }) + "\n",
);
