// Run original and corrected ADC classes against independently produced bytes.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { resultAdcClass, RESULT_PROFILE } from "./reference_adc_results.mjs";

const [root, corpusPath] = process.argv.slice(2);
const { Adc } = await import(pathToFileURL(path.join(root, "src/adc.js")));
const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
const corpus = fs.readFileSync(corpusPath);
if (corpus.length !== 1048576 * 4) throw Error("Incomplete independent ADC corpus");
const profiles = {};
for (const [name, Class] of [
  ["original", Adc],
  [RESULT_PROFILE, resultAdcClass(Adc)],
]) {
  const scheduler = new Scheduler();
  const via = {
    cb1: 0,
    setcb1(value) {
      this.cb1 = +value;
    },
  };
  const adc = new Class(via, scheduler);
  const levels = [0, 0, 0, 0];
  for (let channel = 0; channel < 4; channel++)
    adc.setChannelSource(channel, { getValue: () => levels[channel], dispose() {} });
  let cases = 0,
    differences = 0;
  for (let control = 0; control < 16; control++) {
    levels.fill(0);
    for (let value = 0; value < 65536; value++) {
      levels[control & 3] = value;
      adc.write(0, control);
      scheduler.polltime(control & 8 ? 20000 : 8000);
      // Sample EOC before any accepted high-byte read acknowledges it.
      const completedEoc = via.cb1;
      const actual =
        (adc.read(0) | (adc.read(1) << 8) | (adc.read(2) << 16) | (completedEoc << 24)) >>> 0;
      if (actual !== corpus.readUInt32LE(cases * 4)) differences++;
      cases++;
    }
  }
  profiles[name] = { cases, differences };
}
const passed = profiles[RESULT_PROFILE].differences === 0 && profiles.original.differences > 0;
console.log(JSON.stringify({ passed, profiles }));
process.exitCode = passed ? 0 : 1;
