// Original VIA control-edge and port-latch observations. Only external pins
// are supplied by the fixture; register/interrupt behaviour is unchanged.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { UserVia } = await import(pathToFileURL(root + "/src/via.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const rows = [];
let cases = 0,
  observations = 0;
for (let polarity = 0; polarity < 4; polarity++)
  for (let independent = 0; independent < 4; independent++)
    for (let acr = 0; acr < 4; acr++)
      for (const outputs of [0, 1]) {
        const scheduler = new Scheduler(),
          cpu = { interrupt: 0 };
        let pa = 255,
          pb = 255;
        const via = new UserVia(cpu, scheduler, false, {
          write() {},
          read() {
            return pb;
          },
        });
        via.drivePortA = () => {
          via.portapins &= pa;
        };
        via.recalculatePortAPins();
        via.recalculatePortBPins();
        rows.push(`case ${cases++}`);
        const operation = (kind, address, data, levels = null) => {
          scheduler.polltime(2);
          if (levels !== null) {
            via.setca1(!!(levels & 1));
            via.setca2(!!(levels & 2));
            via.setcb1(!!(levels & 4));
            via.setcb2(!!(levels & 8));
          }
          let result = 0;
          if (kind === "write") via.write(address, data);
          if (kind === "read") result = via.read(address);
          rows.push(
            `${kind} ${address} ${data} ${levels ?? 16} ${result} ${via.read(13)} ${!!cpu.interrupt ? 1 : 0}`,
          );
          observations++;
        };
        const pins = (a, b) => {
          pa = a;
          pb = b;
          via.recalculatePortAPins();
          via.recalculatePortBPins();
          rows.push(`pins ${a} ${b}`);
        };
        const pcr =
          (polarity & 1 ? 5 : 0) |
          (polarity & 2 ? 0x50 : 0) |
          (independent & 1 ? 2 : 0) |
          (independent & 2 ? 0x20 : 0);
        operation("write", 12, pcr);
        operation("write", 14, 0x9b);
        operation("write", 11, acr);
        operation("write", 3, outputs ? 255 : 0);
        operation("write", 2, outputs ? 0xf0 : 0);
        operation("write", 15, 0xa5);
        operation("write", 0, 0xa5);
        operation("write", 13, 0x7f);
        pins(0x34, 0x5b);
        operation("read", 15, 0);
        operation("read", 0, 0);
        operation("edge", 0, 0, 15);
        operation("edge", 0, 0, 0);
        pins(0x56, 0x6c);
        operation("read", 15, 0);
        operation("read", 0, 0);
        // Repeated edges must refresh latches even when IFR is already set.
        operation("edge", 0, 0, 15);
        operation("edge", 0, 0, 0);
        pins(0x78, 0x7d);
        operation("read", 15, 0);
        operation("read", 1, 0);
        operation("read", 0, 0);
        // A simultaneous control edge precedes the port's acknowledgement.
        operation("read", 1, 0, 15);
        operation("read", 0, 0, 0);
        operation("write", 13, 0x7f, 15);
        operation("read", 13, 0);
      }
fs.writeFileSync(output, rows.join("\n") + "\n");
console.log(`Original VIA edges: ${cases} cases; ${observations} observations`);
