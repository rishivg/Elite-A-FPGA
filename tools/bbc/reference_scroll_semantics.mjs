// Check the proof's small transition model against unchanged original APIs.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out] = process.argv.slice(2);
const { SysVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
let scroll = 0;
const via = new SysVia({ interrupt: 0 }, new Scheduler(), {
  video: {
    setScreenHwScroll(value) {
      scroll = value;
    },
  },
  soundChip: { updateSlowDataBus() {} },
  isMaster: false,
  initialLayout: "physical",
});
let latchCases = 0,
  portCases = 0;
for (let latch = 0; latch < 256; latch++)
  for (let port = 0; port < 256; port++) {
    // Diagnostic initial states are independent cases, never installed in DUT.
    via.IC32 = latch;
    via.portbpins = port;
    via.portBUpdated();
    const mask = 1 << (port & 7);
    const expected = port & 8 ? latch | mask : latch & ~mask;
    if (via.IC32 !== expected || scroll !== ((expected >>> 4) & 3))
      throw Error("Original IC32 transition differs from proof model");
    latchCases++;
  }
// Only PB0..3 select IC32. All ORB/DDRB low-nibble transitions are covered;
// inactive/active fire inputs and arbitrary upper output bits cannot affect it.
for (let orb = 0; orb < 16; orb++)
  for (let ddr = 0; ddr < 16; ddr++)
    for (let data = 0; data < 16; data++)
      for (const address of [0, 2]) {
        via.orb = orb;
        via.ddrb = ddr;
        via.write(address, data);
        const output = address === 0 ? data : orb;
        const direction = address === 2 ? data : ddr;
        if ((via.portbpins & 15) !== ((output | ~direction) & 15))
          throw Error("Original port transition differs from proof model");
        portCases++;
      }
fs.writeFileSync(
  out,
  JSON.stringify({ passed: true, latch_cases: latchCases, port_cases: portCases }),
);
console.log(`Original scroll semantics: ${latchCases} latch and ${portCases} port transitions`);
