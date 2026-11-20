// Original device APIs supply callback/bus ordering; no reference state enters RTL.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const [root, adapter, directory] = process.argv.slice(2);
const { Cpu6502 } = await import(pathToFileURL(root + "/src/6502.js"));
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { cycleCpu } = await import(pathToFileURL(adapter));
const writes = [
  [0, 0x35],
  [1, 0x0d],
  [1, 0],
  [1, 0],
  [1, 1],
  [0, 0x53],
  [1, 0],
  [1, 0],
  [1, 0x21],
];
const cases = [{ address: 0, write: false, offset: 3, acknowledge: false }];
for (const [address, write] of [
  [1, false],
  [4, false],
  [7, false],
  [4, true],
  [7, true],
])
  for (const offset of [-1, 0, 1]) cases.push({ address, write, offset, acknowledge: true });
const rows = [],
  reports = [];

// A spy CPU engine records adapter ordering, not CPU correctness. Native C
// sampling semantics are supplied separately by the sealed sampling theorem.
const proxy = directory + "/engine-proxy.cjs";
fs.writeFileSync(proxy, "module.exports=globalThis.__bbcCallbackEngine;\n");
const require = createRequire(import.meta.url);
const engine = { init() {}, tick() {}, sampleNmi() {}, regs() {} };
globalThis.__bbcCallbackEngine = engine;
for (let id = 0; id < cases.length; id++) {
  const scenario = cases[id],
    scheduler = new Scheduler();
  const cpu = Object.create(Cpu6502.prototype);
  Object.assign(cpu, { _nmiSources: 0, _nmiEdge: false, interrupt: 0 });
  const fdc = new IntelFdc(cpu, scheduler);
  for (const [address, data] of writes) {
    scheduler.polltime(1);
    fdc.write(address, data);
  }
  while (!cpu.nmi && scheduler.epoch < 20010) scheduler.polltime(1);
  const deadline = scheduler.epoch;
  if (deadline !== 16009) throw Error("Changed independently executed cold command deadline");
  // Repeat from native construction; the preceding run only discovers its deadline.
  const actualScheduler = new Scheduler(),
    actualCpu = Object.create(Cpu6502.prototype);
  Object.assign(actualCpu, { _nmiSources: 0, _nmiEdge: false, interrupt: 0 });
  const actualFdc = new IntelFdc(actualCpu, actualScheduler);
  let readValue = 0;
  for (let cycle = 1; cycle <= deadline + 3; cycle++) {
    actualScheduler.polltime(1);
    if (cycle <= writes.length) actualFdc.write(...writes[cycle - 1]);
    if (scenario.acknowledge && cycle === deadline + scenario.offset) {
      if (scenario.write) actualFdc.write(scenario.address, 0x5a);
      else readValue = actualFdc.read(scenario.address);
    }
  }
  if (!actualCpu._nmiEdge) throw Error("Original callback edge was not retained");
  rows.push(
    [
      id,
      deadline,
      scenario.acknowledge ? deadline + scenario.offset : 0,
      +scenario.write,
      scenario.address,
      readValue,
      +actualCpu.nmi,
      +actualCpu._nmiEdge,
    ].join(" "),
  );

  // Exercise the maintained cycle adapter with the same real FDC and original
  // polltime implementation. Synthetic request pins isolate ordering only.
  const orderScheduler = new Scheduler(),
    orderCpu = Object.create(Cpu6502.prototype);
  Object.assign(orderCpu, {
    _nmiSources: 0,
    _nmiEdge: false,
    interrupt: 0,
    model: {},
    cpuMultiplier: 1,
    videoCyclesBatch: 0,
    currentCycles: 0,
    targetCycles: 0,
    hasTube: false,
    teletextAdaptor: null,
    music5000: null,
    econet: null,
    scheduler: orderScheduler,
    video: { polltime() {} },
    p: { setFromByte() {} },
  });
  const orderFdc = new IntelFdc(orderCpu, orderScheduler);
  orderCpu.fdc = orderFdc;
  orderCpu.polltime = Cpu6502.prototype.buildPolltime.call(orderCpu);
  orderCpu.is1MHzAccess = Cpu6502.prototype.buildIs1MHzAccess.call(orderCpu);
  const events = [];
  let tick = 0,
    retained = false;
  function request(index) {
    if (index < writes.length) return (0xfe80 + writes[index][0]) | (writes[index][1] << 16);
    if (scenario.acknowledge && index + 1 === deadline + scenario.offset)
      return (0xfe80 + scenario.address) | (scenario.write ? 0x5a << 16 : 0x01000000);
    return 0x01002000;
  }
  engine.init = () => {
    tick = 0;
    retained = false;
    return request(0);
  };
  engine.sampleNmi = (level) => {
    if (level) retained = true;
    events.push([orderCpu.currentCycles, "sample", +level]);
  };
  engine.tick = (pins) => {
    events.push([orderCpu.currentCycles, "tick", +!!(pins & 0x08000000)]);
    return (request(++tick) | (pins & 0x0c000000)) >>> 0;
  };
  engine.regs = () => ({ a: 0, x: 0, y: 0, s: 0, p: 0, pc: 0 });
  orderCpu.peekmem = () => 0;
  orderCpu.readmem = (address) => {
    events.push([orderCpu.currentCycles, "read", address]);
    return address >= 0xfe80 ? orderFdc.read(address & 7) : 0xea;
  };
  orderCpu.writemem = (address, data) => {
    events.push([orderCpu.currentCycles, "write", address]);
    if (address >= 0xfe80) orderFdc.write(address & 7, data);
  };
  cycleCpu(orderCpu, proxy);
  orderCpu.execute(deadline + 3);
  const boundary = events.filter(([cycle]) => cycle >= deadline - 1 && cycle <= deadline + 1);
  const rise = events.findIndex(
    ([cycle, action, level]) => cycle === deadline && action === "sample" && level === 1,
  );
  const bus = events.findIndex(
    ([cycle, action]) => cycle === deadline && (action === "read" || action === "write"),
  );
  if (!retained || rise < 0 || bus < 0 || rise >= bus)
    throw Error("Cycle adapter did not retain callback before same-slot acknowledgement");
  reports.push({
    id,
    ...scenario,
    deadline,
    boundary,
    native_nmi_edge: +actualCpu._nmiEdge,
    final_level: +actualCpu.nmi,
  });
}
delete globalThis.__bbcCallbackEngine;
delete require.cache[require.resolve(proxy)];
fs.writeFileSync(directory + "/fdc.tsv", rows.join("\n") + "\n");
let irqCases = 0;
for (let flags = 0; flags < 128; flags++)
  for (let enabled = 0; enabled < 128; enabled++) {
    const via = { ifr: flags, ier: enabled, cpu: { interrupt: 0 }, irq: 2 };
    UserVia.prototype.updateIFR.call(via);
    if (!!(via.cpu.interrupt & 2) !== !!(flags & enabled))
      throw Error("Original IRQ reduction changed");
    irqCases++;
  }
fs.writeFileSync(
  directory + "/source.json",
  JSON.stringify({
    passed: true,
    cases: reports,
    irq_cases: irqCases,
    scope:
      "Cold FDC commands and local VIA IRQ reduction; synthetic CPU request pins check adapter ordering only.",
  }) + "\n",
);
