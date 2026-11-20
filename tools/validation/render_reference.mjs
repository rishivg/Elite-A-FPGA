// Observe the actual released LL9 routine. Debug hooks do not change guest RAM,
// CPU registers or elapsed time. Face output comes from the executing 6502.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { bootReleased } from "./reference_boot.mjs";
import { memorySnapshot } from "./reference_memory.mjs";
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const s = config.symbols,
  session = await bootReleased(config),
  cpu = session._machine.processor;
const { BBC } = await import(pathToFileURL(path.join(config.jsbeeb, "src/keymap.js")));
const read = (addr, size = 1) => Array.from(session.readMemory(addr, size));
const word = (addr) => {
  const b = read(addr, 2);
  return b[0] + 256 * b[1];
};
const faces = [],
  ships = [],
  transforms = [],
  orientations = [],
  vertices = [];
let pending = null,
  current = null,
  pendingTransform = null,
  pendingOrientation = null,
  pendingVertex = null;
const vertexEntry = s.LL49 + 13; // Immediately after the verified JSR LL51.
const hook = cpu.debugInstruction.add((pc) => {
  if (pc === s.LL9) {
    const pointer = word(s.XX0);
    // Addresses are checked against the released flight binary, since the
    // docked overlay has a different LL9 at a different address.
    if (read(s.LL86)[0] !== 0xb1 || read(s.LL88)[0] !== 0xc4) return false;
    current = {
      index: ships.length,
      cycle: session.elapsedCycles,
      pointer,
      type: read(s.TYPE)[0],
      inwk: read(s.INWK, 37),
      blueprint_header: read(pointer, 20),
    };
    ships.push(current);
  }
  if (pc === s.LL86 && current) {
    if (pending) throw Error("Face capture overlapped");
    const normal = read((word(s.V) + cpu.y) & 65535, 4),
      dot = read(s.XX18, 9);
    pending = {
      ship: current.index,
      face: cpu.y >> 2,
      normal,
      dot_magnitude: [dot[0], dot[3], dot[6]],
      dot_sign: [dot[2] >> 7, dot[5] >> 7, dot[8] >> 7],
      scale: read(s.XX17)[0],
      distance: read(s.XX4)[0],
    };
  }
  if (pc === s.LL88 && pending) {
    const dot = read(s.XX18, 9);
    faces.push({
      ...pending,
      visibility: read(s.XX2 + pending.face)[0],
      next_dot_magnitude: [dot[0], dot[3], dot[6]],
    });
    pending = null;
  }
  if (pc === s.ll91 && pendingOrientation) {
    orientations.push({ ...pendingOrientation, result: read(s.XX16, 18) });
    pendingOrientation = null;
  }
  if (pc === s.LL17 && current && current.type < 128 && read(pc)[0] === 0xa2) {
    if (pendingOrientation) throw Error("Orientation capture overlapped");
    pendingOrientation = {
      ship: current.index,
      cycle: session.elapsedCycles,
      raw: read(s.XX1 + 9, 18),
      transpose: 0,
    };
  }
  if (pendingTransform && pc === pendingTransform.return_pc) {
    transforms.push({ ...pendingTransform, result: read(s.XX12, 6) });
    pendingTransform = null;
  }
  if (pc === vertexEntry && current) {
    if (read(vertexEntry - 3, 3).join(",") !== [0x20, s.LL51 & 255, s.LL51 >> 8].join(","))
      throw Error("Wrong vertex translation entry");
    if (pendingVertex) throw Error("Vertex capture overlapped");
    pendingVertex = {
      ship: current.index,
      transform: transforms.length - 1,
      world: read(s.XX1, 9),
      rotated: read(s.XX12, 6),
    };
  }
  if (pc === s.LL60 && pendingVertex)
    pendingVertex.reduced = [...read(s.XX15, 6), ...read(s.T), ...read(s.U)];
  if (pc === s.LL50 && pendingVertex) {
    if (!pendingVertex.reduced) throw Error("Missing original vertex scaling");
    vertices.push({ ...pendingVertex, screen: read(s.XX3 + read(s.CNT)[0], 4) });
    pendingVertex = null;
  }
  if (pc === s.LL51 && current && current.type < 128 && read(pc)[0] === 0xa2) {
    if (pendingTransform) throw Error("Transform capture overlapped");
    const lo = read(0x100 + ((cpu.s + 1) & 255))[0],
      hi = read(0x100 + ((cpu.s + 2) & 255))[0];
    pendingTransform = {
      ship: current.index,
      cycle: session.elapsedCycles,
      return_pc: (lo + 256 * hi + 1) & 65535,
      vector: read(s.XX15, 6),
      matrix: read(s.XX16, 18),
    };
  }
  return false;
});
session.keyDownRaw(BBC.F0);
await session.runFor(2000000);
session.releaseAllKeys();
await session.runFor(10000000);
for (let wait = 0; read(s.QQ12)[0] & 128 && wait < 20; wait++) await session.runFor(1000000);
if (read(s.QQ12)[0] & 128) throw Error("Original game did not launch");
session.keyDownRaw(BBC.F1);
await session.runFor(2000000);
session.releaseAllKeys();
await session.runFor(4000000);
// Vary real flight inputs, so the routine sees different ship orientations.
for (const keys of [["PERIOD", "X", "SPACE"], [], ["COMMA", "S"], [], ["A"], []]) {
  session.releaseAllKeys();
  for (const key of keys) session.keyDownRaw(BBC[key]);
  await session.runFor(600000);
}
hook.remove();
fs.mkdirSync(config.output, { recursive: true });
fs.writeFileSync(path.join(config.output, "faces.json"), JSON.stringify(faces) + "\n");
fs.writeFileSync(path.join(config.output, "ships.json"), JSON.stringify(ships) + "\n");
fs.writeFileSync(path.join(config.output, "transforms.json"), JSON.stringify(transforms) + "\n");
fs.writeFileSync(
  path.join(config.output, "orientations.json"),
  JSON.stringify(orientations) + "\n",
);
fs.writeFileSync(path.join(config.output, "vertices.json"), JSON.stringify(vertices) + "\n");
fs.writeFileSync(path.join(config.output, "runtime.ram"), memorySnapshot(cpu));
fs.writeFileSync(
  path.join(config.output, "summary.json"),
  JSON.stringify({
    faces: faces.length,
    ships: ships.length,
    transforms: transforms.length,
    orientations: orientations.length,
    vertices: vertices.length,
    cycles: session.elapsedCycles,
    view: read(s.QQ11)[0],
    docked: !!(read(s.QQ12)[0] & 128),
    source: "unmodified released Elite-A LL9 execution; real BBC keyboard launch/rear-view/flight",
  }) + "\n",
);
if (
  faces.length < 100 ||
  ships.length < 10 ||
  transforms.length < 100 ||
  orientations.length < 20 ||
  vertices.length < 100
)
  throw Error("Not enough original renderer observations");
