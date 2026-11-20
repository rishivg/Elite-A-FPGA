// Original LL9 vertex translation/scaling/projection; isolated operand injection.
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
const put = (address, bytes) => session.writeMemory(address, Uint8Array.from(bytes));
const read = (address, size) => Array.from(session.readMemory(address, size));
const entry = s.LL49 + 13;
if (
  read(entry - 3, 3).join(",") !== [0x20, s.LL51 & 255, s.LL51 >> 8].join(",") ||
  read(entry, 1)[0] !== 0xa5 ||
  read(s.LL60, 1)[0] !== 0xa5 ||
  read(s.LL50, 1)[0] !== 0x18
)
  throw Error("Wrong original vertex entry/exit opcodes");
let stop = 0;
const hook = cpu.debugInstruction.add((pc) => pc === stop);
function execute(begin, end, stack = 255) {
  stop = end;
  cpu.pc = begin;
  cpu.a = 0;
  cpu.x = 0;
  cpu.y = 0;
  cpu.s = stack;
  cpu.p.i = true;
  cpu.p.d = false;
  cpu.takeInt = false;
  cpu.breakpointResume = false;
  cpu.targetCycles = cpu.currentCycles;
  if (cpu.execute(100000) !== false || cpu.pc !== stop)
    throw Error("Original vertex routine failed");
}
function project(reduced) {
  put(s.XX15, reduced.slice(0, 6));
  put(s.T, [reduced[6]]);
  put(s.U, [reduced[7]]);
  put(s.CNT, [16]);
  execute(s.LL60, s.LL50);
  return read(s.XX3 + 16, 4);
}
const vertices = [];
for (const item of config.cases) {
  put(s.XX1, item.world);
  put(s.XX12, item.rotated);
  execute(entry, s.LL60);
  const reduced = [...read(s.XX15, 6), ...read(s.T, 1), ...read(s.U, 1)],
    screen = project(reduced);
  if (item.reduced && reduced.some((v, i) => v !== item.reduced[i]))
    throw Error("Natural vertex translation differs");
  if (item.screen && screen.some((v, i) => v !== item.screen[i]))
    throw Error("Natural vertex projection differs");
  vertices.push([...item.world, ...item.rotated, ...reduced, ...screen].join("\t"));
}
fs.writeFileSync(config.output, vertices.join("\n") + "\n");
const projections = [];
for (const item of config.projections) projections.push([...item, ...project(item)].join("\t"));
fs.writeFileSync(config.projection_output, projections.join("\n") + "\n");
const rawRows = [];
for (const item of config.raw_cases || []) {
  put(s.XX1 + 9, item.raw);
  execute(s.LL17, s.ll91);
  put(s.XX0, [0, 0x58]);
  put(0x5808, [6]);
  execute(s.LL42, s.LL48);
  const matrix = read(s.XX16, 18);
  put(s.XX15, item.vector);
  put(0x1fe, [3, 0x58]);
  execute(s.LL51, 0x5804, 253);
  const rotated = read(s.XX12, 6);
  put(s.XX1, item.world);
  execute(entry, s.LL60);
  const reduced = [...read(s.XX15, 6), ...read(s.T, 1), ...read(s.U, 1)],
    screen = project(reduced);
  rawRows.push(
    [...item.raw, ...item.vector, ...item.world, ...matrix, ...rotated, ...reduced, ...screen].join(
      "\t",
    ),
  );
}
if (config.raw_output) fs.writeFileSync(config.raw_output, rawRows.join("\n") + "\n");
hook.remove();
fs.writeFileSync(
  config.output + ".json",
  JSON.stringify({
    cases: vertices.length,
    projections: projections.length,
    raw_vertices: rawRows.length,
    source:
      "unchanged released LL9 vertex translation/scaling/projection; isolated operand injection",
  }) + "\n",
);
