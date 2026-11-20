// Original public command/timer histories at the maintained cold epoch.
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
for (const kind of ["rate", "zero", "cancel", "replace"]) {
  for (const offset of [-1, 0, 1]) {
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
      const value = action === "W" ? (fdc.write(address, data), data) : fdc.read(address);
      rows.push([scheduler.epoch, action, address, value, fdc.read(0), +nmi, edges].join(" "));
      return value;
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
    command(0x69, 3);
    const start = scheduler.epoch,
      first = start + 4000,
      oldDeadline = start + 16000;
    let deadline;
    if (kind === "zero") {
      // No-parameter WRITE SPECIAL reuses the old parameter RAM. Its target
      // here is R3, leaving the outstanding mechanical operation intact.
      at(first + offset, "W", 0, 0x78);
      deadline = oldDeadline;
      at(deadline - 1);
      assert.equal(edges, 0);
      at(deadline);
      assert.equal(edges, 1);
    } else if (kind === "rate") {
      at(first + offset - 2, "W", 0, 0x7a);
      record("W", 1, 0x0d);
      record("W", 1, 2);
      const second = first + (offset < 0 ? 8000 : 4000),
        third = second + 8000;
      deadline = third + 4000;
      for (const cycle of [
        ...new Set([
          first + 3,
          second - 1,
          second,
          oldDeadline,
          third - 1,
          third,
          deadline - 1,
          deadline,
        ]),
      ].sort((a, b) => a - b)) {
        at(cycle);
        assert.equal(edges, cycle === deadline ? 1 : 0);
      }
    } else {
      if (kind === "cancel") {
        at(first + offset, "W", 0, 0x6c);
        record("R", 1);
        at(first + 100);
        command(0x69, 4);
      } else {
        at(first + offset - 1, "W", 0, 0x69);
        record("W", 1, 4);
      }
      deadline = scheduler.epoch + 8000;
      at(deadline - 1);
      assert.equal(edges, 0);
      at(deadline);
      assert.equal(edges, 1);
    }
    assert.equal(nmi, true);
    assert.equal(record("R", 1), 0);
    assert.equal(nmi, false);
    at(Math.max(oldDeadline + 1, scheduler.epoch + 100));
    assert.equal(edges, 1);
    const name = `${kind}-${offset}`;
    lines.push(`case ${reports.length} ${name} ${rows.length}`, ...rows);
    reports.push({
      name,
      kind,
      offset,
      start,
      first,
      oldDeadline,
      deadline,
      rows: rows.length,
      transitions,
    });
  }
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({ passed: true, cases: reports }) + "\n",
);

// Finite original scheduler projection, including queued timers labelled none.
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
const rates = [];
let cases = 0;
for (let rate = 0; rate < 256; rate++) {
  fdc._regs[0x0d] = rate;
  rates.push(fdc._stepRateMs());
  for (const oldState of [0, 1, 2]) {
    for (const queued of [false, true]) {
      for (const oldDelay of [0, 1, 17, 4000]) {
        for (const state of [1, 2]) {
          fdc.reset();
          fdc._regs.fill(0);
          fdc._regs[0x0d] = rate;
          fdc._callContext = 2;
          fdc._timerState = oldState;
          if (queued) fdc._timerTask.schedule(oldDelay);
          const start = scheduler.epoch,
            delay = fdc._stepRateMs() * 2000;
          fdc._setTimerMs(state, fdc._stepRateMs());
          assert.equal(fdc._timerTask.scheduled(), true);
          assert.equal(fdc._timerTask.expireEpoch, start + delay);
          assert.equal(scheduler.scheduled, fdc._timerTask);
          assert.equal(fdc._timerTask.next, null);
          edges = 0;
          scheduler.polltime(delay - 1);
          assert.equal(edges, 0);
          scheduler.polltime(1);
          assert.equal(edges, 1);
          assert.equal(fdc._timerTask.scheduled(), false);
          assert.equal(fdc._result, 0x10);
          scheduler.polltime(oldDelay + 1);
          assert.equal(edges, 1);
          cases++;
        }
      }
    }
  }
}
fs.writeFileSync(
  directory + "/scheduler.json",
  JSON.stringify({ passed: true, cases, step_ms: rates }) + "\n",
);
