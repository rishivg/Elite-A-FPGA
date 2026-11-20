// Original source-setter closure and executing native CPU callback regressions.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, adapter, library, driveHelper, discPath, mode] = process.argv.slice(2);
const { Cpu6502, NmiSource } = await import(pathToFileURL(root + "/src/6502.js"));
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
const { cycleCpu, captureNmiTransitions } = await import(pathToFileURL(adapter));
assert.deepEqual(Object.values(NmiSource), [1, 2, 4]);

function context(mask = 0, edge = false) {
  const cpu = Object.create(Cpu6502.prototype);
  Object.assign(cpu, { _nmiSources: mask, _nmiEdge: edge });
  return cpu;
}

function domain() {
  let cases = 0;
  for (let mask = 0; mask < 8; mask++)
    for (const edge of [false, true])
      for (const source of Object.values(NmiSource))
        for (const level of [false, true]) {
          const original = context(mask, edge),
            observed = context(mask, edge),
            samples = [];
          captureNmiTransitions(observed, (value) => samples.push(value));
          const before = original.nmi;
          const expectedReturn = original.setNmi(source, level);
          assert.equal(observed.setNmi(source, level), expectedReturn);
          assert.equal(observed._nmiSources, original._nmiSources);
          assert.equal(observed._nmiEdge, original._nmiEdge);
          assert.deepEqual(samples, before === original.nmi ? [before] : [before, original.nmi]);
          assert.ok(observed._nmiSources >= 0 && observed._nmiSources < 8);
          cases++;
        }
  assert.equal(cases, 96);
  assert.throws(() => captureNmiTransitions({}, () => {}), /no NMI source setter/);
  // Preserve the original receiver and return value even when it is overridden.
  const cpu = context();
  cpu.setNmi = function (source, level) {
    assert.equal(this, cpu);
    Cpu6502.prototype.setNmi.call(this, source, level);
    return 17;
  };
  captureNmiTransitions(cpu, () => {});
  assert.equal(cpu.setNmi(1, true), 17);
  return { passed: true, cases };
}

function machine() {
  const cpu = context();
  Object.assign(cpu, {
    interrupt: 0,
    model: { isMaster: false },
    cpuMultiplier: 1,
    videoCyclesBatch: 0,
    currentCycles: 0,
    targetCycles: 0,
    hasTube: false,
    teletextAdaptor: null,
    music5000: null,
    econet: null,
    scheduler: new Scheduler(),
    video: { polltime() {} },
    p: { setFromByte() {} },
  });
  const fdc = new IntelFdc(cpu, cpu.scheduler);
  cpu.fdc = fdc;
  cpu.polltime = Cpu6502.prototype.buildPolltime.call(cpu);
  cpu.is1MHzAccess = () => false;
  const memory = new Uint8Array(65536);
  memory.fill(0xea);
  memory[0x2000] = 0;
  memory.set([0x4c, 0, 4], 0x400);
  memory.set([0xee, 0, 0x20, 0x40], 0x500);
  memory.set([0, 5, 0, 4], 0xfffa);
  cpu.readmem = (address) => memory[address];
  cpu.peekmem = cpu.readmem;
  cpu.writemem = (address, data) => (memory[address] = data);
  const control = cycleCpu(cpu, library);
  const transitions = [],
    setter = cpu.setNmi;
  cpu.setNmi = function (source, level) {
    const before = cpu.nmi;
    const result = setter.call(this, source, level);
    if (before !== cpu.nmi) transitions.push([cpu.scheduler.epoch, +cpu.nmi]);
    return result;
  };
  const run = (count) => cpu.execute(count);
  return { cpu, fdc, memory, control, transitions, run };
}

function lifecycle() {
  const { cpu, fdc, memory, control, run } = machine();
  run(64);
  cpu.setNmi(NmiSource.fdc, true);
  run(64);
  assert.equal(memory[0x2000], 1);
  cpu.setNmi(NmiSource.econet, true);
  cpu.setNmi(NmiSource.fdc, false);
  cpu.setNmi(NmiSource.fdc, true);
  run(64);
  assert.equal(memory[0x2000], 1, "Merged sources must not invent a second edge");
  cpu.setNmi(NmiSource.fdc, false);
  cpu.setNmi(NmiSource.econet, false);
  cpu.setNmi(NmiSource.tube, true);
  run(64);
  assert.equal(memory[0x2000], 2);
  cpu.setNmi(NmiSource.tube, false);
  run(64);

  // Save only own native snapshots; do not inject or manufacture CPU state.
  // Restore source line first, as the production reference restore does.
  for (const [high, settle] of [
    [false, true],
    [true, true],
    [true, false],
  ]) {
    cpu.setNmi(NmiSource.fdc, high);
    if (settle) run(64);
    const saved = {
      native: control.snapshot(),
      memory: memory.slice(),
      cycles: cpu.currentCycles,
      target: cpu.targetCycles,
      scheduler: cpu.scheduler.snapshotState(),
      fdc: fdc.snapshotState(),
    };
    const continuation = () => {
      cpu.setNmi(NmiSource.fdc, false);
      cpu.setNmi(NmiSource.fdc, true);
      run(64);
      return { native: control.snapshot(), memory: memory.slice(), cycles: cpu.currentCycles };
    };
    const expected = continuation();
    cpu.scheduler.restoreState(saved.scheduler);
    fdc.restoreState(saved.fdc);
    cpu.setNmi(NmiSource.fdc, high);
    memory.set(saved.memory);
    cpu.currentCycles = saved.cycles;
    cpu.targetCycles = saved.target;
    control.restore(saved.native);
    assert.deepEqual(continuation(), expected, "Own restore must preserve pending events");
    // Reset withdraws the original FDC line without erasing a captured edge.
    cpu.setNmi(NmiSource.fdc, false);
    cpu.setNmi(NmiSource.fdc, true);
    fdc.reset();
    assert.equal(cpu.nmi, false);
    const before = memory[0x2000];
    run(64);
    assert.equal(memory[0x2000], before + 1);
  }
  return { passed: true, handlers: memory[0x2000], restored_levels: 2, restored_pending: true };
}

function abort() {
  const { cpu, fdc, memory, transitions, run } = machine();
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(discPath))));
  configureDrive(fdc, "clocked-fm");
  function write(address, data) {
    cpu.polltime(1);
    fdc.write(address, data);
  }
  function command(op, ...parameters) {
    write(0, op);
    for (const value of parameters) write(1, value);
  }
  command(0x35, 0x0d, 1, 0, 0xf0);
  command(0x35, 0x10, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x3a, 0x23, 0x48);
  cpu.polltime(850000);
  command(0x6c);
  fdc.read(1);
  command(0x6c);
  assert.ok(fdc.read(1) & 4, "Drive must be ready");
  command(0x53, 0, 0, 0x21);
  cpu.targetCycles = cpu.currentCycles;
  while (!cpu.nmi && cpu.currentCycles < 1500000) run(128);
  assert.equal(cpu.nmi, true, "READ must request its first byte");
  run(512);
  const status = fdc.read(0),
    result = fdc.read(1);
  assert.equal(status, 28);
  assert.equal(result, 10);
  assert.equal(transitions.length, 4);
  assert.equal(transitions[1][0] - transitions[0][0], 128);
  assert.equal(transitions[1][0], transitions[2][0]);
  assert.deepEqual(
    transitions.map((row) => row[1]),
    [1, 0, 1, 0],
  );
  // Negative controls must reach this precise behavioral assertion.
  assert.equal(memory[0x2000], 2, "ABORT_RETRIGGER_LOST");
  return { passed: true, handlers: memory[0x2000], status, result, transitions };
}

const checks = { domain, lifecycle, abort };
assert.ok(Object.hasOwn(checks, mode), "Unknown event qualification mode");
console.log(JSON.stringify(checks[mode]()));
