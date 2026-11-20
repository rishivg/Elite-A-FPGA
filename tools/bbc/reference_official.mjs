// Original instruction engine checks independently declared component inputs.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, stimulus, fixture, variant] = process.argv.slice(2);
if (variant !== undefined && variant !== "all-values") throw Error("Unknown official CPU variant");
const { fake6502 } = await import(pathToFileURL(root + "/src/fake6502.js"));
const cpu = fake6502(undefined, { cycleAccurate: true });
await cpu.initialise();
const pristine = cpu.snapshotState(),
  inputs = fs.readFileSync(stimulus, "utf8").trim().split("\n"),
  cases = fs.readFileSync(fixture, "utf8").trim().split("\n");
const expectedCount = 9920 * (variant === "all-values" ? 64 : 1);
if (inputs.length !== expectedCount || cases.length !== expectedCount)
  throw Error("Incomplete documented-opcode fixture");
let differences = 0;
let brkCases = 0;
for (let index = 0; index < cases.length; index++) {
  const [opcode, mode, value, flags, boundary, duration, pc, a, x, y, s, p] = cases[index]
    .split(" ")
    .slice(0, 12)
    .map(Number);
  const fields = inputs[index].split(" "),
    [base, target] = fields.slice(0, 2).map(Number);
  if (opcode === 0) brkCases++;
  cpu.restoreState(pristine);
  cpu.ramRomOs.fill(0xea, 0, 65536);
  for (const entry of fields.slice(2)) {
    const [address, data] = entry.split(":").map(Number);
    cpu.writemem(address, data);
  }
  cpu.pc = base;
  cpu.p.i = true;
  cpu.polltime = (clocks) => {
    cpu.currentCycles += clocks;
  };
  let instructions = 0;
  while (cpu.pc !== target) {
    if (instructions++ > 20) throw Error("Component input prefix did not complete");
    cpu.targetCycles = cpu.currentCycles;
    cpu.execute(1);
  }
  const start = cpu.currentCycles;
  cpu.targetCycles = cpu.currentCycles;
  cpu.execute(1);
  if (
    cpu.pc !== pc ||
    (opcode === 0 ? cpu.currentCycles - start !== 9 : cpu.currentCycles - start !== duration) ||
    cpu.a !== a ||
    cpu.x !== x ||
    cpu.y !== y ||
    cpu.s !== s ||
    (cpu.p.asByte() & 0xcf) !== p
  ) {
    if (differences++ < 16)
      console.log({
        opcode,
        mode,
        value,
        flags,
        boundary,
        expected: [pc, duration, a, x, y, s, p],
        actual: [
          cpu.pc,
          cpu.currentCycles - start,
          cpu.a,
          cpu.x,
          cpu.y,
          cpu.s,
          cpu.p.asByte() & 0xcf,
        ],
      });
  }
}
// The original BRK handler adds its generated two clocks to brk(false)'s
// seven clocks. Preserve that independent baseline: check its known nine
// clocks explicitly, while the pin engine and NMOS specification use seven.
console.log(
  `Original / cycle CPU: ${cases.length - differences}/${cases.length} architectural results; ${cases.length - brkCases} instruction durations; ${brkCases} original BRK durations are 9 versus NMOS 7`,
);
if (differences) process.exit(1);
