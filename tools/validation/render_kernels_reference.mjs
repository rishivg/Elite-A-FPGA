// Isolated kernel oracle: execute unchanged released 6502 bytes. Injecting
// operands is intentional here; these are not full-game replay checkpoints.
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
  ["LL86", 0xb1],
  ["LL88", 0xc4],
])
  if (session.readMemory(s[label], 1)[0] !== opcode)
    throw Error(`Wrong flight overlay at ${label}`);
const read = (addr) => session.readMemory(addr, 1)[0];
const put = (addr, ...bytes) => session.writeMemory(addr, Uint8Array.from(bytes));
const hook = cpu.debugInstruction.add((pc) => pc === s.LL88);
const rows = [];
let cycles = 0;
for (const item of config.cases) {
  put(0x5800, ...item.normal);
  put(s.V, 0, 0x58);
  put(s.XX20, 4);
  put(s.XX17, item.scale);
  put(s.XX4, item.distance);
  put(s.XX2, 0x55);
  for (let axis = 0; axis < 3; axis++)
    put(s.XX18 + 3 * axis, item.dot_magnitude[axis], 0, item.dot_sign[axis] << 7);
  cpu.pc = s.LL86;
  cpu.a = 0;
  cpu.x = 0;
  cpu.y = 0;
  cpu.s = 255;
  cpu.p.i = true;
  cpu.p.d = false;
  cpu.takeInt = false;
  cpu.breakpointResume = false;
  cpu.targetCycles = cpu.currentCycles;
  const before = cpu.cycleSeconds * 2000000 + cpu.currentCycles;
  if (cpu.execute(100000) !== false || cpu.pc !== s.LL88)
    throw Error(`Original face kernel did not finish: ${rows.length}`);
  cycles += cpu.cycleSeconds * 2000000 + cpu.currentCycles - before;
  const signs = item.dot_sign.reduce((word, bit, index) => word | (bit << index), 0);
  rows.push(
    [
      ...item.normal,
      ...item.dot_magnitude,
      signs,
      item.scale,
      item.distance,
      read(s.XX2),
      read(s.XX18),
      read(s.XX18 + 3),
      read(s.XX18 + 6),
    ].join("\t"),
  );
}
hook.remove();
fs.writeFileSync(config.output, rows.join("\n") + "\n");
fs.writeFileSync(
  config.output + ".json",
  JSON.stringify({
    cases: rows.length,
    cycles,
    source:
      "unchanged released 1.F 6502 face kernel; isolated operand injection, not full-game replay",
  }) + "\n",
);
