// Independently cold writable-media and recovery histories.
import fs from "node:fs";
import { createCase, emitCase } from "./reference_disc_media_base.mjs";
const [root, ssd, output] = process.argv.slice(2);
const cases = [];
for (const name of [
  "normal",
  "deleted-normal",
  "deleted-both",
  "deleted-verify",
  "reset-deleted",
  "rewrite-normal",
  "abort-one",
  "abort-two",
  "abort-request",
  "late-first",
  "late-second",
  "dma-write",
  "deleted-scan0",
  "deleted-scan1",
  "deleted-dma-normal",
  "deleted-dma-both",
  "rewrite-abort",
]) {
  const test = await createCase(root, ssd);
  const { command, transfer, ready, write } = test;
  if (
    [
      "normal",
      "deleted-normal",
      "deleted-both",
      "deleted-verify",
      "reset-deleted",
      "rewrite-normal",
      "abort-one",
      "abort-two",
    ].includes(name)
  ) {
    transfer(
      name.startsWith("abort-") || name === "normal" ? 0x4b : 0x4f,
      name === "abort-one" ? 1 : name === "abort-two" ? 2 : 0,
    );
    if (name === "reset-deleted" || name.startsWith("abort-")) {
      if (!name.startsWith("abort-")) write(2, 1);
      ready();
    }
    if (name === "rewrite-normal") transfer(0x4b);
    transfer(name === "deleted-both" ? 0x57 : name === "deleted-verify" ? 0x5f : 0x53);
  } else if (name.startsWith("deleted-")) {
    transfer(0x4f);
    if (name.includes("dma")) command(0x7a, 0x17, 0xc0);
    transfer(
      name === "deleted-scan0"
        ? 0x40
        : name === "deleted-scan1"
          ? 0x44
          : name === "deleted-dma-both"
            ? 0x57
            : 0x53,
    );
  } else {
    if (name === "dma-write") command(0x7a, 0x17, 0xc0);
    transfer(
      0x4b,
      name === "abort-request" ? -1 : name === "rewrite-abort" ? 2 : 0,
      name === "late-first" ? 0 : name === "late-second" ? 1 : -1,
    );
    if (name === "abort-request" || name === "rewrite-abort" || name.startsWith("late-")) {
      if (name.startsWith("late-")) write(2, 1);
      ready();
    }
    if (name === "dma-write") command(0x7a, 0x17, 0xc1);
    if (name === "rewrite-abort") transfer(0x4b);
    transfer(0x53);
  }
  cases.push(emitCase(output, name, test));
}
fs.writeFileSync(output, JSON.stringify({ own_cold_cases: true, cases }) + "\n");
