// Execute the pinned Model B methods for every address, direction and phase.
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, output] = process.argv.slice(2);
const { Cpu6502 } = await import(pathToFileURL(root + "/src/6502.js"));
const context = { model: { isMaster: false } };
const isSlow = Cpu6502.prototype.buildIs1MHzAccess.call(context);
let slowBits = 0n;
let cases = 0;
for (let address = 0; address < 65536; address++) {
  const slow = isSlow(address, false);
  if (slow) slowBits |= 1n << BigInt(address);
  for (const write of [false, true]) {
    if (isSlow(address, write) !== slow) throw Error("Model B direction-dependent bus");
    for (const parity of [0, 1]) {
      const cpu = {
        currentCycles: parity,
        is1MHzAccess: isSlow,
        polltime(cycles) {
          this.currentCycles += cycles;
        },
      };
      Cpu6502.prototype.polltimeAddr.call(cpu, 1, address, write);
      const duration = cpu.currentCycles - parity;
      if (duration !== (slow ? 2 + ((1 ^ parity) & 1) : 1))
        throw Error("Original bus duration differs from the cycle adapter");
      cases++;
    }
  }
}
fs.writeFileSync(
  output,
  JSON.stringify({ passed: true, cases, slow_hex: slowBits.toString(16).padStart(16384, "0") }) +
    "\n",
);
