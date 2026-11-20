// Public command interference around an observed original retry callback.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
function machine(profile = "retry") {
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
    retries = [];
  const seek = fdc._doSeek;
  fdc._doSeek = function (context) {
    if (context === 1) retries.push(scheduler.epoch);
    return seek.call(this, context);
  };
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
  };
  const at = (cycle, action = "P", address = 0, data = 0) =>
    record(action, address, data, cycle - scheduler.epoch);
  command(0x35, 0x0d, 1, profile === "spec-pointer" ? 1 : 0, 0xf0);
  command(0x35, 0x10, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x7a, 0x23, 0x48);
  if (profile === "seek") {
    command(0x69, 3);
    return { scheduler, fdc, rows, retries, record, command, at };
  }
  record("P", 0, 0, 850000);
  command(0x6c);
  record("R", 1);
  command(0x6c);
  assert.ok(record("R", 1) & 4);
  if (profile === "index") {
    command(0x53, 0, 10, 0x21);
    return { scheduler, fdc, rows, retries, record, command, at };
  }
  if (profile === "transfer") {
    command(0x53, 0, 0, 0x21);
    return { scheduler, fdc, rows, retries, record, command, at };
  }
  if (profile === "spec-pointer") {
    // A rephased ID retry starts a millisecond timer on the host slot.
    // This keeps the pointer collision reachable after normal timers were
    // aligned to the earlier drive-callback phase.
    command(0x7a, 0x12, 3);
    command(0x53, 3, 0, 0x21);
    command(0x7a, 0x23, 0x48);
    command(0x7a, 0x07, 3);
    return { scheduler, fdc, rows, retries, record, command, at };
  }
  command(0x7a, 0x12, 3);
  command(0x53, 3, 0, 0x21);
  return { scheduler, fdc, rows, retries, record, command, at };
}
const control = machine();
while (!control.retries.length && control.scheduler.epoch < 1000000) control.scheduler.polltime(1);
assert.equal(control.retries.length, 1);
const deadline = control.retries[0];
const lines = [],
  cases = [];
for (const count of [1, 2, 3])
  for (const offset of [-1, 0, 1]) {
    const m = machine();
    if (offset >= 0) m.at(deadline - 1);
    if (offset > 0) m.at(deadline);
    m.at(deadline + offset, "W", 0, 0x50 | count);
    if (offset < 0) m.at(deadline);
    m.at(deadline + 3);
    const beforeRead = m.fdc._regs[0x12];
    m.command(0x7d, 0x12);
    assert.equal(m.record("R", 1), beforeRead);
    m.record("W", 2, 1);
    m.at(deadline + 30000);
    const name = `r1-${count}-${offset}`;
    lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
    cases.push({ name, count, offset, deadline, rows: m.rows.length, track: beforeRead });
  }
for (const address of [0x0a, 0x0b, 0x0c])
  for (const offset of [100, 4000, 8000]) {
    const m = machine("seek"),
      start = m.scheduler.epoch;
    m.at(start + offset);
    m.command(0x7d, address);
    const returned = m.record("R", 1);
    m.record("W", 2, 1);
    const name = `seek-bank-${address}-${offset}`;
    lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
    cases.push({ name, address, offset, rows: m.rows.length, returned });
  }
const specControl = machine("spec-pointer");
while (!specControl.retries.length && specControl.scheduler.epoch < 1000000)
  specControl.scheduler.polltime(1);
assert.equal(specControl.retries.length, 1);
const specDeadline = specControl.retries[0] + 8000;
for (const offset of [-1, 0, 1]) {
  const m = machine("spec-pointer"),
    deadline = specDeadline;
  m.at(deadline - 200, "W", 0, 0x75);
  m.record("W", 1, 0x0d);
  if (offset >= 0) m.at(deadline - 1);
  if (offset > 0) m.at(deadline);
  m.at(deadline + offset, "W", 1, 0x0d);
  if (offset < 0) m.at(deadline);
  m.at(deadline + 2, "W", 1, 1);
  m.record("W", 1, 0xf0);
  m.command(0x7d, 0x0d);
  const returned = m.record("R", 1);
  assert.equal(returned, offset < 0 ? 13 : 1);
  m.record("W", 2, 1);
  const name = `spec-pointer-${offset}`;
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, offset, deadline, rows: m.rows.length, returned });
}
for (const value of [0, 1, 2, 3, 255]) {
  const m = machine("seek");
  m.record("W", 2, 1);
  m.command(0x75, 1);
  m.record("W", 1, value);
  for (let i = 0; i < 4; i++) m.record("W", 1, 0);
  m.record("P", 0);
  m.record("W", 2, 1);
  const name = `spec-r1-${value}`;
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, value, rows: m.rows.length });
}
for (const offset of [-1, 0, 1]) {
  const m = machine();
  m.at(deadline - 2, "W", 0, 0x51);
  if (offset >= 0) m.at(deadline - 1);
  if (offset > 0) m.at(deadline);
  m.at(deadline + offset, "W", 1, 1);
  if (offset < 0) m.at(deadline);
  m.at(deadline + 3);
  m.command(0x7d, 0x12);
  const returned = m.record("R", 1);
  m.record("W", 2, 1);
  const name = `r1-param-${offset}`;
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, offset, deadline, rows: m.rows.length, returned });
}
const indexControl = machine("index");
while (indexControl.fdc._regs[0x1c] === 3 && indexControl.scheduler.epoch < 1500000)
  indexControl.scheduler.polltime(1);
assert.equal(indexControl.fdc._regs[0x1c], 2);
const indexDeadline = indexControl.scheduler.epoch;
for (const value of [0, 1, 2, 3, 255]) {
  const m = machine("index");
  m.at(indexDeadline - 3, "W", 0, 0x7a);
  m.record("W", 1, 0x1c);
  m.record("W", 1, value);
  m.at(indexDeadline);
  m.at(indexDeadline + 3);
  m.command(0x7d, 0x1c);
  const returned = m.record("R", 1);
  assert.equal(returned, (value - 1) & 255);
  m.record("W", 2, 1);
  const name = `index-register-${value}`;
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, value, deadline: indexDeadline, rows: m.rows.length, returned });
}
{
  const m = machine("transfer");
  while (!(m.fdc.read(0) & 8) && m.scheduler.epoch < 1500000) m.scheduler.polltime(1);
  const first = m.scheduler.epoch,
    deadline = first + 128;
  m.record("P", 0, 0, 0);
  m.command(0x7a, 0x0d, 2);
  m.at(deadline - 1);
  m.at(deadline);
  m.at(deadline + 3);
  assert.equal(m.record("R", 1), 0x0a);
  m.record("W", 2, 1);
  const name = "programmed-transfer";
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, deadline, rows: m.rows.length });
}
for (const offset of [-1, 0, 1]) {
  const m = machine();
  if (offset >= 0) m.at(deadline - 1);
  if (offset > 0) m.at(deadline);
  m.at(deadline + offset, "W", 0, 0x69);
  if (offset < 0) m.at(deadline);
  m.at(deadline + 20000);
  assert.equal(m.fdc.read(0) & 8, 0);
  m.record("W", 2, 1);
  const name = `retry-owner-${offset}`;
  lines.push(`case ${cases.length} ${name} ${m.rows.length}`, ...m.rows);
  cases.push({ name, offset, deadline, rows: m.rows.length });
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({ passed: true, deadline, cases }) + "\n",
);
