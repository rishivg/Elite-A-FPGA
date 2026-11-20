// Compare the compiled physical sampler with the original asynchronous latch.
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, trace, output] = process.argv.slice(2);
const { Cpu6502 } = await import(pathToFileURL(root + "/src/6502.js"));
let cases = 0;
for (const line of fs.readFileSync(trace, "utf8").trim().split("\n")) {
  const [initial, retained, pattern, ...edges] = line.split(" ").map(Number);
  const cpu = { _nmiSources: initial, _nmiEdge: !!retained };
  for (let bit = 0; bit < 4; ++bit) {
    Cpu6502.prototype.setNmi.call(cpu, 1, !!(pattern & (1 << bit)));
    if (Number(cpu._nmiEdge) !== edges[bit])
      throw Error("Physical NMI capture differs from original JS");
  }
  cases++;
}
if (cases !== 64) throw Error("Incomplete physical NMI trace inventory");
fs.writeFileSync(output, JSON.stringify({ passed: true, cases, observations: cases * 4 }) + "\n");
