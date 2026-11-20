// Five controller boundaries, using independent cold public histories.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
const kinds = [
  "mmio-clock",
  "mmio-data",
  "mmio-drive",
  "mmio-read",
  "spec-late",
  "spec-cancel",
  "head",
  "settle",
  "settle-zero",
  "active-settle",
  "parameter",
];
const lines = [],
  reports = [];
for (const kind of kinds)
  for (const offset of [-1, 0, 1]) {
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
    fdc._log = () => {};
    fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(discPath))));
    configureDrive(fdc, "clocked-fm");
    scheduler.polltime(16);
    const rows = [];
    function record(action, address, data = 0, advance = 1) {
      assert.ok(Number.isInteger(advance) && advance >= 0);
      scheduler.polltime(advance);
      const value = action === "W" ? (fdc.write(address, data), data) : fdc.read(address);
      rows.push([scheduler.epoch, action, address, value, fdc.read(0), +nmi, edges].join(" "));
      return value;
    }
    function command(op, ...args) {
      record("W", 0, op);
      for (const v of args) record("W", 1, v);
    }
    const at = (t, action = "P", address = 0, data = 0) =>
      record(action, address, data, t - scheduler.epoch);
    command(0x35, 0x0d, 1, 1, kind === "head" ? 0xf1 : 0xf0);
    command(0x35, 0x10, 255, 255, 0);
    command(0x3a, 0x17, 0xc1);
    const loaded = !kind.startsWith("spec-") && kind !== "parameter" && kind !== "head";
    if (loaded) command(0x7a, 0x23, 0x48);
    const warm = kind === "mmio-read";
    if (warm) {
      record("P", 0, 0, 850000);
      command(0x6c);
      record("R", 1);
      command(0x6c);
      assert.ok(record("R", 1) & 4);
    }
    if (kind.startsWith("spec-") || kind === "parameter") {
      // A cold head-load callback will fail READY at a known deadline.
      command(0x35, 0x0f, 1, 0, 0); // head=1, then unused adjacent bytes
      command(0x53, 0, 0, 0x21);
      const start = scheduler.epoch,
        deadline = start + 16000;
      at(start + 100, "W", 0, kind === "parameter" ? 0x53 : 0x35);
      if (kind !== "parameter") record("W", 1, 0x0d);
      if (kind === "spec-cancel") {
        at(start + 200 + offset, "W", 1, 2);
        record("W", 1, 1);
        record("W", 1, 0xf0);
        at(deadline + 100);
        assert.equal(edges, 0);
        assert.equal(nmi, false);
      } else {
        if (offset >= 0) at(deadline - 1);
        if (offset > 0) at(deadline);
        at(deadline + offset, "W", 1, kind === "parameter" ? 0x0d : 2);
        if (offset < 0) at(deadline);
        at(deadline + 3);
        assert.equal(edges, 1);
        assert.equal(nmi, true);
        assert.equal(record("R", 1), 0x10);
        command(0x7c);
        assert.equal(
          record("R", 1),
          kind === "parameter" ? (offset < 0 ? 1 : 7) : offset < 0 ? 2 : 1,
        );
      }
      const name = `${kind}-${offset}`;
      lines.push(`case ${reports.length} ${name} ${rows.length}`, ...rows);
      reports.push({ name, kind, offset, start, deadline, rows: rows.length, transitions });
      continue;
    }
    command(0x69, kind === "mmio-read" ? 1 : 3);
    const start = scheduler.epoch,
      first = start + 4000;
    let deadline = start + (kind === "mmio-read" ? 8000 : 16000);
    let atWrite = first + offset;
    if (kind === "active-settle") atWrite = start + 12100 + offset;
    const target =
      kind === "head"
        ? 0x0f
        : kind.startsWith("settle") || kind === "active-settle"
          ? 0x0e
          : kind === "mmio-clock"
            ? 0x24
            : kind === "mmio-data"
              ? 0x25
              : 0x23;
    const value =
      kind === "head"
        ? 0xf2
        : kind === "settle" || kind === "active-settle"
          ? 3
          : kind === "settle-zero"
            ? 0
            : kind === "mmio-clock"
              ? 0xff
              : kind === "mmio-data"
                ? 0xa5
                : 0x48;
    at(atWrite - 2, "W", 0, 0x7a);
    record("W", 1, target);
    record("W", 1, value);
    if (kind === "head") deadline = start + 44000;
    if (kind === "settle") deadline = start + 24000;
    if (kind === "settle-zero") deadline = start + 12000;
    at(deadline - 1);
    assert.equal(edges, 0);
    at(deadline);
    assert.equal(edges, 1);
    assert.equal(nmi, true);
    assert.equal(record("R", 1), warm ? 0 : 0x10);
    if (kind === "mmio-drive") {
      command(0x6c);
      assert.equal(!!(record("R", 1) & 2), offset < 0, "Later pulses must use live seek direction");
    }
    if (kind === "mmio-read") {
      command(0x53, 1, 0, 0x21);
      let before;
      while (!nmi && scheduler.epoch < 2000000) {
        before = [scheduler.epoch, "P", 0, fdc.read(0), fdc.read(0), +nmi, edges].join(" ");
        scheduler.polltime(1);
      }
      assert.equal(nmi, true);
      if (+before.split(" ")[0] > +rows.at(-1).split(" ")[0]) rows.push(before);
      record("P", 0, 0, 0);
      record("R", 4);
      record("W", 2, 1);
    }
    const name = `${kind}-${offset}`;
    lines.push(`case ${reports.length} ${name} ${rows.length}`, ...rows);
    reports.push({ name, kind, offset, start, deadline, rows: rows.length, transitions });
  }
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({ passed: true, cases: reports }) + "\n",
);
