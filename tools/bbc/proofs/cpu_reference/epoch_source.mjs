// Execute the actual machine initializer, maintained reset block and slot adapter.
// A spy engine isolates timing/order; it supplies no CPU correctness evidence.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, referencePath, adapterPath, directory] = process.argv.slice(2);
const { MachineSession } = await import(pathToFileURL(root + "/src/machine-session.js"));
const { Cpu6502 } = await import(pathToFileURL(root + "/src/6502.js"));
const { cycleCpu } = await import(pathToFileURL(adapterPath));
const session = new MachineSession("B-DFS1.2", { tube: false });
await session.initialise();
const cpu = session._machine.processor;
if (
  cpu.currentCycles !== 0 ||
  cpu.scheduler.epoch !== 0 ||
  cpu.cpuMultiplier !== 1 ||
  cpu.videoCyclesBatch !== 0
)
  throw Error("Original machine has a different initial clock profile");
const reference = fs.readFileSync(referencePath, "utf8");
const begin = reference.indexOf('if (!config.load_state && powerProfile === "shared-reset") {');
const end = reference.indexOf("\nconst videoClock", begin);
if (begin < 0 || end < begin) throw Error("Changed maintained reset block boundary");
const resetBlock = reference.slice(begin, end);
// Execute the exact maintained block with real initialized original devices.
new Function("cpu", "session", "chip", "config", "powerProfile", resetBlock)(
  cpu,
  session,
  session._soundChip,
  {},
  "shared-reset",
);
if (cpu.currentCycles !== 16 || cpu.scheduler.epoch !== 16)
  throw Error("Maintained shared-reset source epoch differs");
const addresses = [0x2000, 0xfe40, 0xfe80, 0xfe08, 0xc000, 0xfe60, 0xfe84, 0xfe00];
const requests = Array.from({ length: 64 }, (_, i) => addresses[i % addresses.length]);
let expected = 16;
const expectedTicks = requests.map((address) => {
  const context = {
    currentCycles: expected,
    is1MHzAccess: cpu.is1MHzAccess,
    polltime(cycles) {
      this.currentCycles += cycles;
    },
  };
  Cpu6502.prototype.polltimeAddr.call(context, 1, address, false);
  expected = context.currentCycles;
  return expected;
});
const callbacks = [],
  bus = [],
  ticks = [];
const callback = cpu.scheduler.newTask(() => {
  callbacks.push([cpu.currentCycles, cpu.scheduler.epoch]);
  callback.schedule(1);
});
callback.schedule(1);
const read = cpu.readmem.bind(cpu);
cpu.readmem = (address) => {
  bus.push([cpu.currentCycles, cpu.scheduler.epoch, address]);
  if (callbacks.at(-1)?.[1] !== cpu.scheduler.epoch)
    throw Error("Bus access precedes original scheduler callback");
  return read(address);
};
let next = 0;
const pins = () => 0x01000000 | (requests[next] ?? 0x2000);
globalThis.__bbcEpochEngine = {
  init: () => pins(),
  sampleNmi() {},
  tick() {
    ticks.push([cpu.currentCycles, cpu.scheduler.epoch]);
    next++;
    return pins();
  },
  regs: () => ({ a: 0, x: 0, y: 0, s: 0, p: 4, pc: 0 }),
};
const proxy = directory + "/engine-proxy.cjs";
fs.writeFileSync(proxy, "module.exports=globalThis.__bbcEpochEngine;\n");
cycleCpu(cpu, proxy);
cpu.targetCycles = cpu.currentCycles;
cpu.execute(expected - 16);
delete globalThis.__bbcEpochEngine;
if (
  next !== requests.length ||
  callbacks.length !== expected - 16 ||
  callbacks.some(([current, epoch], i) => current !== 17 + i || epoch !== current) ||
  ticks.some(([current, epoch], i) => current !== expectedTicks[i] || epoch !== current) ||
  bus.some(
    ([current, epoch, address], i) =>
      current !== expectedTicks[i] || epoch !== current || address !== requests[i],
  )
)
  throw Error("Original scheduler/CPU epoch drifts through mixed fast/slow requests");
fs.writeFileSync(
  directory + "/source-origin.json",
  JSON.stringify({
    passed: true,
    constructor_epoch: 0,
    shared_reset_epoch: 16,
    source_slots: expected - 16,
    bus_requests: requests.length,
    callbacks,
    bus,
    ticks,
    scope:
      "Actual original machine initialization and exact maintained reset block, then real peripheral polling through the actual adapter with synthetic CPU bus pins.",
  }) + "\n",
);
