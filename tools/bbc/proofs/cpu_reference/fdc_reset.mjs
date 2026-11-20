// Finite original control projections. These are not injected RTL states.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const scheduler = new Scheduler();
let nmi = false,
  rises = 0;
const fdc = new IntelFdc(
  {
    setNmi(_source, level) {
      if (level && !nmi) rises++;
      nmi = level;
    },
    nmiAsserted: () => nmi,
  },
  scheduler,
);
fdc._log = () => {};

function quiet() {
  assert.equal(fdc._paramCallback, 0);
  assert.equal(fdc._indexPulseCallback, 1);
  assert.equal(fdc._timerState, 0);
  assert.equal(fdc._state, 1);
  assert.equal(fdc._driveOut, 0);
  assert.equal(fdc._currentDrive, null);
  assert.equal(fdc.internalStatus, 0);
  assert.equal(nmi, false);
}

let controlCases = 0,
  inertTimers = 0;
for (let parameter = 0; parameter < 3; parameter++) {
  for (let index = 1; index <= 6; index++) {
    for (let timer = 0; timer < 3; timer++) {
      for (let state = 0; state < 25; state++) {
        for (let queued = 0; queued < 2; queued++) {
          fdc._regs.fill(0xa5);
          fdc._paramCallback = parameter;
          fdc._indexPulseCallback = index;
          fdc._timerState = timer;
          fdc._state = state;
          fdc._stateCount = 0xff;
          fdc._isResultReady = true;
          fdc._mmioData = 0x5a;
          nmi = true;
          if (queued) fdc._timerTask.schedule(1);
          const before = [...fdc._regs];
          fdc.reset();
          quiet();
          assert.equal(fdc._stateCount, 0);
          assert.equal(fdc._isResultReady, true);
          assert.equal(fdc._mmioData, 0x5a);
          before[0x17] = 0;
          assert.deepEqual([...fdc._regs], before);
          assert.equal(fdc._timerTask.scheduled(), !!queued && timer === 0);
          const priorRises = rises;
          scheduler.polltime(1);
          quiet();
          assert.equal(rises, priorRises);
          assert.equal(fdc._timerTask.scheduled(), false);
          if (queued && timer === 0) {
            assert.equal(fdc._regs[8], 0);
            assert.equal(fdc._regs[9], 0);
            inertTimers++;
          }
          controlCases++;
        }
      }
    }
  }
}

let statusCases = 0;
for (let status = 0; status < 256; status++) {
  for (let ready = 0; ready < 2; ready++) {
    fdc._regs[0x17] = status;
    fdc._regs[0x16] = status ^ 0xff;
    fdc._regs[0x1d] = status;
    fdc._isResultReady = !!ready;
    nmi = true;
    fdc.reset();
    quiet();
    assert.equal(fdc.read(0), ready ? 0x10 : 0);
    assert.equal(fdc._regs[0x16], status ^ 0xff);
    assert.equal(fdc._regs[0x1d], status);
    assert.equal(fdc._isResultReady, !!ready);
    statusCases++;
  }
}

let driveCases = 0;
for (let driveOut = 0; driveOut < 256; driveOut++) {
  // The original cannot abort an open write gate without a selected drive.
  const selected = driveOut & 0xc0;
  if (driveOut & 1 && selected !== 0x40 && selected !== 0x80) continue;
  fdc._setDriveOut(driveOut);
  const oldDrive = fdc._currentDrive;
  const priorRises = rises;
  fdc.reset();
  quiet();
  if (oldDrive) assert.equal(oldDrive._spinning, false);
  assert.equal(rises, priorRises);
  scheduler.polltime(100);
  quiet();
  driveCases++;
}

// Exhaust reset dispatch separately from delegated command/parameter semantics.
const dispatch = new IntelFdc({ setNmi() {}, nmiAsserted: () => false }, new Scheduler());
let resetCalls = 0,
  dispatchCases = 0;
dispatch.reset = () => resetCalls++;
dispatch._commandWritten = () => {};
dispatch._paramWritten = () => {};
dispatch._log = () => {};
for (let address = 0; address < 8; address++) {
  for (let data = 0; data < 256; data++) {
    const before = resetCalls;
    dispatch.write(0xfe80 + address, data);
    assert.equal(resetCalls - before, address === 2 && data === 1 ? 1 : 0);
    dispatchCases++;
  }
}

// Every byte and host address outside command starts in a reset quiet state.
// Both source timer and FM callbacks are then executed, including stale expiry.
let quietCases = 0;
for (let address = 0; address < 8; address++) {
  for (let data = 0; data < 256; data++) {
    fdc.reset();
    const before = rises;
    if (address === 0) fdc.read(data & 7);
    else fdc.write(0xfe80 + address, data);
    fdc._timerFired();
    fdc._pulsesCallback((data * 0x01010101) >>> 0, 32);
    quiet();
    assert.equal(rises, before);
    quietCases++;
  }
}
fs.writeFileSync(
  output,
  JSON.stringify({
    passed: true,
    control_cases: controlCases,
    status_cases: statusCases,
    drive_cases: driveCases,
    dispatch_cases: dispatchCases,
    quiet_cases: quietCases,
    inert_timers: inertTimers,
  }) + "\n",
);
console.log("Original reset: control/status/drive/dispatch/quiet matrices passed");
