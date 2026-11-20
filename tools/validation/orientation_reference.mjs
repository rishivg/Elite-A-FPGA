// Independent raw-orientation fixtures from unchanged released LL17, LL42,
// LL51 and LL86. Operand injection belongs only to this isolated benchmark.
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
  ["LL17", 0xa2],
  ["ll91", 0xb5],
  ["LL42", 0xa4],
  ["LL48", 0x84],
  ["LL51", 0xa2],
])
  if (session.readMemory(s[label], 1)[0] !== opcode)
    throw Error(`Wrong released overlay at ${label}`);
const put = (addr, ...bytes) => session.writeMemory(addr, Uint8Array.from(bytes));
const read = (addr, size) => Array.from(session.readMemory(addr, size));
let stop = 0,
  cycles = 0;
const hook = cpu.debugInstruction.add((pc) => pc === stop);
function execute(entry, exit, stack = 255) {
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
    throw Error(`Original orientation kernel failed at ${entry.toString(16)}`);
  cycles += cpu.cycleSeconds * 2000000 + cpu.currentCycles - before;
}
function normalize(item) {
  put(s.XX1 + 9, ...item.raw);
  execute(s.LL17, s.ll91);
  if (item.transpose) {
    put(s.XX0, 0, 0x58);
    put(0x5808, 6);
    execute(s.LL42, s.LL48);
  }
  return read(s.XX16, 18);
}
function transform(item) {
  const matrix = normalize(item);
  put(s.XX15, ...item.vector);
  put(0x1fe, 3, 0x58);
  execute(s.LL51, 0x5804, 253);
  const result = read(s.XX12, 6);
  if (item.expected_matrix && matrix.some((v, i) => v !== item.expected_matrix[i]))
    throw Error("Natural orientation/matrix chain differs");
  if (item.expected_result && result.some((v, i) => v !== item.expected_result[i]))
    throw Error("Natural raw-transform chain differs");
  return { matrix, result };
}
const rows = [];
for (const item of config.cases)
  rows.push([...item.raw, item.transpose, ...normalize(item)].join("\t"));
fs.writeFileSync(config.output, rows.join("\n") + "\n");
const matrices = [];
for (const item of config.transforms) {
  const { matrix, result } = transform(item);
  matrices.push([...item.raw, item.transpose, ...item.vector, ...matrix, ...result].join("\t"));
}
fs.writeFileSync(config.transform_output, matrices.join("\n") + "\n");
const faces = [];
for (const item of config.pipelines) {
  const { matrix, result } = transform(item);
  put(0x5800, ...item.normal);
  put(s.V, 0, 0x58);
  put(s.XX20, 4);
  put(s.XX17, item.scale);
  put(s.XX4, item.distance);
  for (let i = 0; i < 3; i++) put(s.XX18 + 3 * i, result[2 * i], 0, result[2 * i + 1]);
  execute(s.LL86, s.LL88);
  faces.push(
    [
      ...item.raw,
      ...item.vector,
      ...matrix,
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
fs.writeFileSync(config.pipeline_output, faces.join("\n") + "\n");
fs.writeFileSync(
  config.output + ".json",
  JSON.stringify({
    cases: rows.length,
    transforms: matrices.length,
    pipelines: faces.length,
    cycles,
    source: "unchanged released LL17/LL42/LL51/LL86; isolated operand injection",
  }) + "\n",
);
