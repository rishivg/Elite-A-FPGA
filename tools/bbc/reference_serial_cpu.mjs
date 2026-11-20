// Normal cold execution of declared test firmware on original BBC devices.
// Firmware is shared input; no RAM, CPU or device state is transferred.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { cycleCpu } from "./reference_cycle.mjs";

const [root, library, firmware, duration, output] = process.argv.slice(2);
const { MachineSession } = await import(pathToFileURL(root + "/src/machine-session.js"));
const session = new MachineSession("B-DFS1.2", { tube: false });
await session.initialise();
const cpu = session._machine.processor;
const rom = fs.readFileSync(firmware);
if (rom.length !== 16384 || !Number.isSafeInteger(+duration) || +duration <= 16)
  throw Error("Invalid declared firmware or duration");
cpu.ramRomOs.set(rom, cpu.osOffset);
cycleCpu(cpu, library);
cpu.currentCycles += 16;
cpu.targetCycles = cpu.currentCycles;
cpu.scheduler.polltime(16);
for (const via of [cpu.sysvia, cpu.uservia]) {
  via.read(13);
  via.reset();
}
session._soundChip.reset(true);
for (const byte of [0x9f, 0xbf, 0xdf, 0xff, 0x80]) session._soundChip.poke(byte);
cpu.sysvia.recalculatePortBPins();
cpu.sysvia.recalculatePortAPins();
session._video.ula.write(0, 0);
const writes = [];
const write = cpu.writemem;
cpu.writemem = function (address, data) {
  const result = write.call(this, address, data);
  if (address < 0x8000) writes.push([cpu.currentCycles * 16, address, data]);
  return result;
};
if (!(await session.runFor(+duration - cpu.currentCycles)).completed)
  throw Error("Original firmware execution stopped early");
fs.writeFileSync(output, writes.map((row) => row.join(" ")).join("\n") + "\n");
console.log(JSON.stringify({ final_cycle: cpu.currentCycles, writes: writes.length }));
