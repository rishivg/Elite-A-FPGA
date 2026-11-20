// Public original abort, reset and retry histories at the maintained source epoch.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
const profiles = [
  ["read-abort", "read"],
  ["write-abort", "write"],
  ["id-abort", "id"],
  ...[-1, 0, 1].map((offset) => [`abort-service-${offset}`, "service", offset]),
  ...[-1, 0, 1].map((offset) => [`abort-reset-${offset}`, "reset", offset]),
  ...[-1, 0, 1].map((offset) => [`head-reset-${offset}`, "cold", offset]),
  ["track-retry", "retry"],
];
const lines = [],
  reports = [];
for (const [name, kind, offset = 0] of profiles) {
  const scheduler = new Scheduler();
  let nmi = false,
    edges = 0;
  const transitions = [];
  const fdc = new IntelFdc(
    {
      setNmi(_source, level) {
        if (level && !nmi) edges++;
        if (level !== nmi) transitions.push([scheduler.epoch, +level]);
        nmi = level;
      },
      nmiAsserted: () => nmi,
    },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(discPath))));
  configureDrive(fdc, "clocked-fm");
  scheduler.polltime(16);
  const rows = [];
  function record(action, address, data = 0, advance = 1) {
    assert.ok(Number.isInteger(advance) && advance >= 0);
    scheduler.polltime(advance);
    let returned = 0;
    if (action === "W") fdc.write(address, data);
    else returned = fdc.read(address);
    rows.push(
      [
        scheduler.epoch,
        action,
        address,
        action === "W" ? data : returned,
        fdc.read(0),
        +nmi,
        edges,
      ].join(" "),
    );
    return returned;
  }
  const at = (cycle, action = "P", address = 0, data = 0) =>
    record(action, address, data, cycle - scheduler.epoch);
  function command(op, ...parameters) {
    record("W", 0, op);
    for (const value of parameters) record("W", 1, value);
  }
  function waitRise(limit = 2500000) {
    let before;
    const previous = edges;
    while (edges === previous && scheduler.epoch < limit) {
      before = [scheduler.epoch, "P", 0, fdc.read(0), fdc.read(0), +nmi, edges].join(" ");
      scheduler.polltime(1);
    }
    assert.equal(edges, previous + 1, "Missing original event");
    if (+before.split(" ")[0] > +rows.at(-1).split(" ")[0]) rows.push(before);
    record("P", 0, 0, 0);
    return scheduler.epoch;
  }
  if (kind === "cold") {
    command(0x35, 0x0d, 0, 0, 1);
    command(0x53, 0, 0, 0x21);
    const deadline = scheduler.epoch + 16000;
    if (offset >= 0) at(deadline - 1);
    if (offset > 0) at(deadline);
    at(deadline + offset, "W", 2, 1);
    if (offset < 0) at(deadline);
    at(deadline + 3);
    assert.equal(edges, offset < 0 ? 0 : 1);
    assert.equal(nmi, false);
    // A replacement public command must own a new timer, not the cancelled one.
    command(0x53, 0, 0, 0x21);
    const next = scheduler.epoch + 16000;
    at(next - 1);
    at(next);
    assert.equal(nmi, true);
    record("R", 1);
  } else {
    command(0x35, 0x0d, 1, 0, 0xf0);
    command(0x35, 0x10, 255, 255, 0);
    command(0x3a, 0x17, 0xc1);
    command(0x3a, 0x23, 0x48);
    record("P", 0, 0, 850000);
    command(0x6c);
    record("R", 1);
    command(0x6c);
    assert.ok(record("R", 1) & 4, "Original drive not ready");
    if (kind === "retry") {
      command(0x7a, 0x12, 3);
      command(0x53, 3, 0, 0x21);
      waitRise(4000000);
      assert.equal(record("R", 1), 0x18);
      command(0x7d, 0x12);
      assert.equal(record("R", 1), 5);
      assert.equal(edges, 1);
    } else {
      command(kind === "write" ? 0x4b : kind === "id" ? 0x5b : 0x53, 0, 0, 0x21);
      const first = waitRise(),
        deadline = first + 128;
      if (kind === "service" || kind === "reset") {
        if (offset >= 0) at(deadline - 1);
        if (offset > 0) at(deadline);
        at(deadline + offset, kind === "service" ? "R" : "W", kind === "service" ? 4 : 2, 1);
        if (offset < 0) at(deadline);
        at(deadline + 3);
        assert.equal(edges, kind === "reset" && offset < 0 ? 1 : 2);
        if (kind === "reset") {
          assert.equal(nmi, false);
          // READ SPECIAL itself sets busy. All pre-reset mode bits must be clear,
          // including when the abort callback and reset share the same slot.
          command(0x7d, 0x17);
          assert.equal(record("R", 1), 0x80);
        }
      } else {
        at(deadline - 1);
        at(deadline);
        assert.equal(edges, 2);
        assert.deepEqual(transitions.slice(-2), [
          [deadline, 0],
          [deadline, 1],
        ]);
      }
      if (kind !== "reset") {
        const result = record("R", 1);
        assert.equal(result, kind === "service" && offset < 0 ? 0 : 10);
      }
    }
  }
  lines.push(`case ${reports.length} ${name} ${rows.length}`, ...rows);
  reports.push({ name, kind, offset, rows: rows.length, edges, transitions });
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({ passed: true, cases: reports }) + "\n",
);
