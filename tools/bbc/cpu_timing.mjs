// Generate independent NMOS IRQ/NMI timing fixtures using the pinned emulator.
// The CPU's RAM bus and clock are the only overridden interfaces.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const [source, output, signal = "irq"] = process.argv.slice(2);
if (!["irq", "nmi"].includes(signal)) throw Error("Expected irq or nmi signal");
const vector = signal === "nmi" ? 0xfffa : 0xfffe;
if (!source || !output) throw Error("Expected verified jsbeeb source and output path");
const { fake6502 } = await import(pathToFileURL(path.join(source, "src/fake6502.js")));
const cases = [
  { name: "nop", code: [0xea] },
  { name: "absolute-read", code: [0xad, 0x00, 0x03] },
  { name: "absolute-write", code: [0x8d, 0x00, 0x03] },
  { name: "branch-taken", code: [0xd0, 0x00] },
  { name: "branch-not-taken", code: [0xf0, 0x00] },
  { name: "sei", code: [0x78] },
  { name: "cli", prefix: [0xa2, 0xff, 0x9a, 0xa9, 0x77, 0xea], code: [0x58] },
  { name: "plp", prefix: [0xa2, 0xff, 0x9a, 0xa9, 0x20, 0x48], code: [0x28] },
  { name: "branch-page-cross", base: 0x4fd, code: [0xd0, 0x02] },
  { name: "rti", prefix: [0xa2, 0xfc, 0x9a, 0xa9, 0x77, 0xea], code: [0x40] },
  { name: "indexed-rmw", code: [0xde, 0, 3] },
  { name: "branch-before-indexed-rmw", code: [0xf0, 0, 0xde, 0, 3] },
  { name: "nop-before-indexed-rmw", code: [0xea, 0xde, 0, 3] },
  { name: "absolute-rmw", code: [0xee, 0, 3] },
];
const rows = [];
// Compile opcode handlers once. Restoring the independent CPU's pristine
// snapshot is much cheaper than rebuilding a whole emulator for every pulse.
const cpu = fake6502(undefined, { cycleAccurate: true });
await cpu.initialise();
const pristine = cpu.snapshotState();
for (let kind = 0; kind < cases.length; kind++)
  for (const width of [0, 1, 2])
    for (let delay = 0; delay < 16; delay++) {
      const test = cases[kind];
      cpu.restoreState(pristine);
      const prefix = test.prefix ?? [0xa2, 0xff, 0x9a, 0xa9, 0x77, 0x58];
      const code = [...prefix, ...test.code, ...Array(128).fill(0xea), 0x4c, 0x06, 0x04];
      const base = test.base ?? 0x406;
      code.forEach((byte, i) => cpu.writemem(base - 6 + i, byte));
      if (kind === 9) {
        cpu.writemem(0x1fd, 0x20);
        cpu.writemem(0x1fe, 0x20);
        cpu.writemem(0x1ff, 4);
      }
      cpu.writemem(0x300, 0xea);
      cpu.writemem(0x3ff, 0xea);
      cpu.writemem(vector, 0);
      cpu.writemem(vector + 1, 0x30);
      cpu.writemem(0x3000, 0x4c);
      cpu.writemem(0x3001, 0);
      cpu.writemem(0x3002, 0x30);
      cpu.pc = base - 6;
      cpu.p.i = true;
      let baseline = null,
        time = 0;
      cpu.polltime = (count) => {
        cpu.currentCycles += count;
        for (let i = 0; i < count; i++) {
          const level =
            baseline !== null &&
            time - baseline >= delay &&
            (!width || time - baseline < delay + width)
              ? 1
              : 0;
          if (signal === "nmi") cpu.setNmi(1, level);
          else cpu.interrupt = level;
          time++;
        }
      };
      while (time < 200 && cpu.pc !== 0x3000) {
        if (cpu.pc === base && baseline === null) baseline = time;
        cpu.targetCycles = cpu.currentCycles;
        cpu.execute(1);
      }
      const taken = cpu.pc === 0x3000;
      rows.push([
        kind,
        delay,
        width,
        taken ? (cpu.readmem(0x1ff) << 8) | cpu.readmem(0x1fe) : 0,
        taken ? time - baseline : -1,
        taken ? cpu.readmem(0x1fd) : 0,
      ]);
    }
fs.writeFileSync(output, rows.map((row) => row.join(" ")).join("\n") + "\n");
console.log(
  `Generated ${rows.length} NMOS ${signal.toUpperCase()} fixtures: ${cases.map((test) => test.name).join(", ")}`,
);
