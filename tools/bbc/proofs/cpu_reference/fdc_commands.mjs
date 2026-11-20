// Public replacement-command histories; each controller owns its cold state.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
const lines = [],
  reports = [];
for (const original of ["read", "seek"]) {
  for (const offset of [-1, 0, 1]) {
    const name = `${original}-partial-${offset}`;
    const scheduler = new Scheduler();
    let nmi = false,
      edges = 0;
    const transitions = [];
    const fdc = new IntelFdc(
      {
        setNmi(_source, level) {
          if (level && !nmi) edges++;
          if (level !== nmi) transitions.push([scheduler.epoch, +level]);
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
      let returned = 0;
      if (action === "W") fdc.write(address, data);
      else returned = fdc.read(address);
      rows.push(
        [
          scheduler.epoch,
          action,
          address,
          action === "W" ? data : returned,
          fdc.read(0),
          +nmi,
          edges,
        ].join(" "),
      );
      return returned;
    }
    function command(op, ...parameters) {
      record("W", 0, op);
      for (const value of parameters) record("W", 1, value);
    }
    const at = (cycle, action = "P", address = 0, data = 0) =>
      record(action, address, data, cycle - scheduler.epoch);
    command(0x35, 0x0d, 1, 1, 0xf0);
    command(0x35, 0x10, 255, 255, 0);
    command(0x3a, 0x17, 0xc1);
    command(0x3a, 0x23, 0x48);
    record("P", 0, 0, 850000);
    command(0x6c);
    record("R", 1);
    command(0x6c);
    assert.ok(record("R", 1) & 4);
    if (original === "read") command(0x53, 1, 0, 0x21);
    else command(0x69, 1);
    const start = scheduler.epoch,
      step = start + 4000,
      deadline = step + 4000;
    at(step - 1);
    at(step);
    if (offset >= 0) at(deadline - 1);
    if (offset > 0) at(deadline);
    // Leave the replacement waiting for parameters through the old deadline.
    at(deadline + offset, "W", 0, original === "read" ? 0x69 : 0x53);
    if (offset < 0) at(deadline);
    at(deadline + 3);
    assert.equal(edges, original === "seek" ? 1 : 0);
    assert.equal(nmi, original === "seek");
    record("W", 2, 1);
    assert.equal(nmi, false);
    at(deadline + 100);
    lines.push(`case ${reports.length} ${name} ${rows.length}`, ...rows);
    reports.push({
      name,
      original,
      offset,
      start,
      step,
      deadline,
      rows: rows.length,
      edges,
      transitions,
    });
  }
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({ passed: true, cases: reports }) + "\n",
);

// Finite original projection, separate from the public cold histories above.
// An arbitrary public command register does not replace the outstanding call.
const scheduler = new Scheduler();
let nmi = false;
const projected = new IntelFdc(
  {
    setNmi(_source, level) {
      nmi = level;
    },
    nmiAsserted: () => nmi,
  },
  scheduler,
);
projected._log = () => {};
let cases = 0;
for (const context of [2, 3, 4, 5]) {
  for (let command = 0; command < 256; command++) {
    for (let ready = 0; ready < 2; ready++) {
      for (let result = 0; result < 256; result++) {
        projected.reset();
        projected._regs.fill(0);
        projected._regs[0x0f] = 0xf0;
        projected._regs[0x17] = 0x80;
        projected._regs[0x1b] = 0xff;
        projected._regs[0x1f] = command;
        projected._regs[0x16] = result;
        projected._regs[5] = 0x21;
        projected._isResultReady = false;
        projected._callContext = context;
        projected._setDriveOut(0x48);
        projected._ready = !!ready;
        projected._readyPulses = ready ? 2 : 0;
        projected._lastIndexEpoch = scheduler.epoch;
        nmi = false;
        projected._postSeekDispatch();
        const complete = !ready || context === 2;
        assert.equal(nmi, complete);
        assert.equal(projected._result, result | (ready ? 0 : 0x10));
        assert.equal(projected._isResultReady, complete);
        assert.equal(!!(projected.internalStatus & 0x80), !complete);
        assert.equal(projected._timerState, 0);
        assert.equal(projected._state, !complete && context !== 3 ? 3 : 1);
        cases++;
      }
    }
  }
}
fs.writeFileSync(directory + "/projection.json", JSON.stringify({ passed: true, cases }) + "\n");
