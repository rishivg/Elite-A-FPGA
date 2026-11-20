// A CPU data read at the same 2 MHz boundary as the first FDC byte callback.
import fs from "node:fs";
import { createCase, emitCase } from "../reference_disc_media_base.mjs";

const [root, ssd, output] = process.argv.slice(2);
const cases = [];
for (const offset of [-1, 0, 1]) {
  const test = await createCase(root, ssd);
  test.command(0x53, 3, 2, 0x21);
  const epoch = 1285577 + offset;
  test.scheduler.polltime(epoch - 1 - test.scheduler.epoch);
  const previous = +test.irq();
  test.scheduler.polltime(1);
  const beforeRead = +test.irq();
  let afterRead;
  test.read(4, (level) => (afterRead = level));
  cases.push({
    ...emitCase(output, `offset-${offset}`, test),
    offset,
    epoch,
    previous,
    beforeRead,
    afterRead,
  });
}
fs.writeFileSync(output, JSON.stringify(cases) + "\n");
