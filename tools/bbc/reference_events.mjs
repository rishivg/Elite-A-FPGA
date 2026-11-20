// Preserve original merged NMI transitions inside scheduler and bus callbacks.
import { createRequire } from "node:module";
import { cycleCpu as stepCpu } from "./reference_cycle.mjs";
const require = createRequire(import.meta.url);

export function captureNmiTransitions(cpu, sample) {
  if (typeof cpu.setNmi !== "function") throw TypeError("Original CPU has no NMI source setter");
  const setNmi = cpu.setNmi;
  sample(!!cpu.nmi);
  cpu.setNmi = function (source, level) {
    const before = !!this.nmi;
    const result = setNmi.call(this, source, level);
    const after = !!this.nmi;
    if (before !== after) sample(after);
    return result;
  };
}

export function cycleCpu(cpu, library) {
  if (typeof cpu.setNmi !== "function") throw TypeError("Original CPU has no NMI source setter");
  const engine = require(library);
  const control = stepCpu(cpu, library);
  captureNmiTransitions(cpu, (level) => engine.sampleNmi(level));
  return control;
}
