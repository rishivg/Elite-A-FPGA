// Each boundary starts from a separately executed cold Original controller.
import fs from "node:fs";
import { createCase, emitCase } from "../reference_disc_media_base.mjs";

const [root, ssd, output] = process.argv.slice(2);
const cases = [];
for (const delta of [-1, 0, 1, 127, 128]) {
  const test = await createCase(root, ssd);
  test.command(0x53, 3, 2, 0x21);
  const target = 1285577 + delta;
  test.scheduler.polltime(target - test.scheduler.epoch);
  const status = test.read(0);
  const data = test.read(status & 4 ? 4 : 1);
  cases.push({ ...emitCase(output, `delta-${delta}`, test), delta, target, status, data });
}
fs.writeFileSync(output, JSON.stringify({ own_cold_cases: true, cases }) + "\n");
