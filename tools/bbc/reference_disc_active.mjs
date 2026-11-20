// A partial new command must preserve the old byte processor and queued data.
import fs from "node:fs";
import { createCase, emitCase } from "./reference_disc_media_base.mjs";

const [root, ssd, output] = process.argv.slice(2);
const cases = [];
for (const op of [0x53, 0x4b]) {
  for (const nextOp of [0x53, 0x4b, 0x5b]) {
    for (const acknowledge of [false, true]) {
      for (const complete of [false, true]) {
        // Completion cases use the opposite transfer direction; the shorter
        // histories also cover READ ID and a second command of the same kind.
        if (complete && nextOp !== (op === 0x53 ? 0x4b : 0x53)) continue;
        const test = await createCase(root, ssd);
        const { scheduler, command, read, write, irq } = test;
        command(op, 3, 2, 0x21);
        const deadline = scheduler.epoch + 8000000;
        let bytes = 0;
        let result = null;
        function awaitIrq() {
          while (!irq() && scheduler.epoch < deadline) scheduler.polltime(1);
          if (!irq()) throw Error("Original byte IRQ missing");
        }
        function service() {
          const status = read(0);
          if (status & 4) {
            if (op === 0x4b) write(4, (bytes * 19 + 3) & 255);
            else read(4);
            bytes++;
          } else result = read(1);
        }
        awaitIrq();
        if (acknowledge) service();
        write(0, nextOp);
        write(1, 3);
        if (complete) {
          while (result === null) {
            awaitIrq();
            service();
          }
          if (bytes !== 256 || result !== 0) throw Error("Original old transfer did not finish");
        } else {
          for (let i = 0; i < 3; i++) {
            awaitIrq();
            service();
            if (result !== null) throw Error("Original old transfer ended early");
          }
          write(2, 1);
        }
        const name = `${op.toString(16)}-${nextOp.toString(16)}-${+acknowledge}-${+complete}`;
        cases.push({
          ...emitCase(output, name, test),
          op,
          nextOp,
          acknowledge,
          complete,
          bytes,
          result,
        });
      }
    }
  }
}
fs.writeFileSync(output, JSON.stringify({ own_cold_cases: true, cases }) + "\n");
