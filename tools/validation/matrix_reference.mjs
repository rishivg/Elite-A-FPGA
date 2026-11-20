// Execute unchanged released LL51 and LL86. These are isolated kernel fixtures,
// with injected operands, distinct from read-only natural gameplay observation.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const { MachineSession } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
);
const session = new MachineSession("B-DFS1.2", { tube: false });
await session.initialise();
await session.boot();
const cpu = session._machine.processor,
  s = config.symbols;
session.writeMemory(0x11e3, fs.readFileSync(config.flight_binary));
for (const [label, opcode] of [
  ["LL51", 0xa2],
  ["LL86", 0xb1],
  ["LL88", 0xc4],
])
  if (session.readMemory(s[label], 1)[0] !== opcode)
    throw Error(`Wrong flight overlay at ${label}`);
const put = (addr, ...bytes) => session.writeMemory(addr, Uint8Array.from(bytes));
const read = (addr, size) => Array.from(session.readMemory(addr, size));
let stop = 0,
  cycles = 0;
const hook = cpu.debugInstruction.add((pc) => pc === stop);
function execute(entry, exit, stack) {
  stop = exit;
  cpu.pc = entry;
  cpu.a = 0;
  cpu.x = 0;
  cpu.y = 0;
  cpu.s = stack;
  cpu.p.i = true;
  cpu.p.d = false;
  cpu.takeInt = false;
  cpu.breakpointResume = false;
  cpu.targetCycles = cpu.currentCycles;
  const before = cpu.cycleSeconds * 2000000 + cpu.currentCycles;
  if (cpu.execute(100000) !== false || cpu.pc !== stop)
    throw Error(`Original kernel failed at ${entry.toString(16)}`);
  cycles += cpu.cycleSeconds * 2000000 + cpu.currentCycles - before;
}
function transform(item) {
  put(s.XX15, ...item.vector);
  put(s.XX16, ...item.matrix);
  put(0x1fe, 3, 0x58); // Synthetic JSR return address, RTS returns to 0x5804.
  execute(s.LL51, 0x5804, 253);
  return read(s.XX12, 6);
}
const rows = [];
for (const item of config.cases)
  rows.push([...item.vector, ...item.matrix, ...transform(item)].join("\t"));
fs.writeFileSync(config.output, rows.join("\n") + "\n");
const pipelines = [];
for (const item of config.pipelines) {
  const result = transform(item);
  put(0x5800, ...item.normal);
  put(s.V, 0, 0x58);
  put(s.XX20, 4);
  put(s.XX17, item.scale);
  put(s.XX4, item.distance);
  for (let axis = 0; axis < 3; axis++)
    put(s.XX18 + 3 * axis, result[2 * axis], 0, result[2 * axis + 1]);
  execute(s.LL86, s.LL88, 255);
  pipelines.push(
    [
      ...item.vector,
      ...item.matrix,
      ...item.normal,
      item.scale,
      item.distance,
      ...result,
      ...read(s.XX2, 1),
      ...read(s.XX18, 1),
      ...read(s.XX18 + 3, 1),
      ...read(s.XX18 + 6, 1),
    ].join("\t"),
  );
}
hook.remove();
fs.writeFileSync(config.pipeline_output, pipelines.join("\n") + "\n");
fs.writeFileSync(
  config.output + ".json",
  JSON.stringify({
    cases: rows.length,
    pipelines: pipelines.length,
    cycles,
    source: "unchanged released 1.F LL51 and LL86; isolated operand injection",
  }) + "\n",
);
