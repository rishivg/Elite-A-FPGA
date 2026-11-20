// Verify path-independent disc loading using complete original public snapshots.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { pathToFileURL } from "node:url";
import { serialize } from "node:v8";
import { deepStrictEqual } from "node:assert";

const [root, disc] = process.argv.slice(2);
if (!root || !disc || process.argv.length !== 4) throw Error("Expected pinned jsbeeb and SSD");
const { MachineSession } = await import(pathToFileURL(path.join(root, "src/machine-session.js")));
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
const directory = fs.mkdtempSync(path.join(os.tmpdir(), "bbc-reference-media-"));
try {
  const first = path.join(directory, "first", "declared.ssd"),
    second = path.join(directory, "second", "declared.ssd");
  for (const file of [first, second]) {
    fs.mkdirSync(path.dirname(file));
    fs.copyFileSync(disc, file);
  }
  async function execute(file, stable) {
    const session = new MachineSession(
      "B-DFS1.2",
      stable ? { tube: false } : { discImage: file, tube: false },
    );
    await session.initialise();
    const cpu = session._machine.processor;
    if (stable)
      cpu.fdc.loadDisc(0, discFor(path.basename(file), new Uint8Array(fs.readFileSync(file))));
    if (!(await session.runFor(64000)).completed) throw Error("Original prefix did not complete");
    return cpu.snapshotState();
  }
  const original = await execute(first, false),
    a = await execute(first, true),
    b = await execute(second, true);
  deepStrictEqual(a, b);
  if (!serialize(a).equals(serialize(b))) throw Error("Serialized own states differ by workspace");
  if (original.fdc.drives[0].disc.name !== first || a.fdc.drives[0].disc.name !== "declared.ssd")
    throw Error("Unexpected original disc name binding");
  // Normalize only the host label in a copied observation, never in a machine.
  original.fdc.drives[0].disc.name = "declared.ssd";
  deepStrictEqual(original, a);
  console.log(
    "Original media labels: 3 independent 64000-cycle prefixes; all machine state preserved; PASS",
  );
} finally {
  fs.rmSync(directory, { recursive: true });
}
