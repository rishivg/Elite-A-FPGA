// Independently check the cycle-core fixture with the original NMOS emulator.
// Its zero-page opcode handlers omit dummy writes, so cross-check the 180
// absolute RMW cases where both original models implement the bus operation.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, fixture] = process.argv.slice(2);
const { fake6502 } = await import(pathToFileURL(root + "/src/fake6502.js"));
const cpu = fake6502(undefined, { cycleAccurate: true });
await cpu.initialise();
const pristine = cpu.snapshotState();
let count = 0;
for (const line of fs.readFileSync(fixture, "utf8").trim().split("\n")) {
  const fields = line.split(" "),
    [opcode, mode, value, carry, duration, a, x, y, s, flags] = fields.slice(0, 10).map(Number);
  if (mode < 2 || mode > 4) continue;
  const requests = fields.slice(10).map((entry) => entry.split(":").map(Number));
  const expectedWrites = requests
    .map((r, i) => [i, r])
    .filter(([, r]) => r[1])
    .map(([i, r]) => [i, r[0], r[2]]);
  cpu.restoreState(pristine);
  const operand = mode === 4 ? 0x20ff : 0x2000,
    target = operand + (mode >= 3 ? 1 : 0);
  const code = [
    0xa2,
    1,
    0xea,
    0xea,
    0xea,
    carry ? 0x38 : 0x18,
    opcode,
    operand & 255,
    operand >> 8,
    0x4c,
    0,
    3,
  ];
  for (let i = 0; i < code.length; i++) cpu.writemem(0x400 + i, code[i]);
  cpu.writemem(target, value);
  cpu.pc = 0x400;
  cpu.p.i = true;
  cpu.polltime = (clocks) => {
    cpu.currentCycles += clocks;
  };
  while (cpu.pc !== 0x406) {
    cpu.targetCycles = cpu.currentCycles;
    cpu.execute(1);
  }
  const baseline = cpu.currentCycles,
    writes = [],
    write = cpu.writemem;
  // The original instruction callback performs its write after polltime's
  // completing edge. Express that end-of-cycle timestamp as a bus-slot index.
  cpu.writemem = (address, data) => {
    writes.push([cpu.currentCycles - baseline - 1, address, data]);
    write.call(cpu, address, data);
  };
  cpu.targetCycles = cpu.currentCycles;
  cpu.execute(1);
  cpu.writemem = write;
  if (
    JSON.stringify(writes) !== JSON.stringify(expectedWrites) ||
    cpu.currentCycles - baseline !== duration ||
    (cpu.p.asByte() & 0xc3) !== flags ||
    cpu.a !== a ||
    cpu.x !== x ||
    cpu.y !== y
  )
    throw Error(
      `Original NMOS / cycle-core fixture differs: opcode ${opcode}, mode ${mode}, value ${value}, carry ${carry}`,
    );
  count++;
}
if (count !== 180) throw Error("Incomplete independent absolute RMW cross-check");
console.log(
  `${count} absolute NMOS read/modify/write fixtures agree between independent original and cycle cores`,
);
