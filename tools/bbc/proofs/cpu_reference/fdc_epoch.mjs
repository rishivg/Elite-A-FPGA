// Original public commands, including service on the last slot before a byte.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
const profiles = [
  ["early-read", 1, "R", 4],
  ["two-slots-read", 126, "R", 4],
  ["last-slot-read", 127, "R", 4],
  ["last-slot-read-alias", 127, "R", 7],
  ["last-slot-write", 127, "W", 4],
  ["last-slot-write-alias", 127, "W", 7],
  ["last-slot-result", 127, "R", 1],
];
const lines = [],
  reports = [];
for (let id = 0; id < profiles.length; id++) {
  const [name, latency, action, address] = profiles[id];
  const scheduler = new Scheduler();
  let nmi = false,
    edges = 0;
  const rises = [];
  const fdc = new IntelFdc(
    {
      setNmi(_source, level) {
        if (level && !nmi) {
          edges++;
          rises.push(scheduler.epoch);
        }
        nmi = level;
      },
      nmiAsserted: () => nmi,
    },
    scheduler,
  );
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(discPath))));
  configureDrive(fdc, "clocked-fm");
  // The maintained shared-reset profile advances the source by sixteen slots.
  scheduler.polltime(16);
  const rows = [];
  function record(action, address, data = 0, advance = 1) {
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
  function command(op, ...parameters) {
    record("W", 0, op);
    for (const p of parameters) record("W", 1, p);
  }
  command(0x35, 0x0d, 1, 0, 0xf0);
  command(0x35, 0x10, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x3a, 0x23, 0x48);
  record("P", 0, 0, 850000);
  command(0x6c);
  record("R", 1);
  command(0x6c);
  if (!(record("R", 1) & 4)) throw Error("Original drive is not ready");
  command(0x53, 0, 0, 0x21);
  let before;
  while (!nmi && scheduler.epoch < 1500000) {
    before = [scheduler.epoch, "P", 0, fdc.read(0), fdc.read(0), +nmi, edges].join(" ");
    scheduler.polltime(1);
  }
  if (!nmi || edges !== 1) throw Error("Missing first original request");
  const first = scheduler.epoch;
  rows.push(before);
  record("P", 0, 0, 0);
  record(action, address, 0x5a, latency);
  if (nmi || edges !== 1) throw Error("Original acknowledgement did not lower NMI");
  if (scheduler.epoch < first + 127) record("P", 0, 0, first + 127 - scheduler.epoch);
  record("P", 0);
  if (!nmi || edges !== 2 || rises[1] !== first + 128)
    throw Error("Changed second original callback deadline");
  record("R", address);
  record("P", 0, 0, 2);
  lines.push(`case ${id} ${name} ${rows.length}`, ...rows);
  reports.push({ name, latency, action, address, first, rises, rows: rows.length });
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({
    passed: true,
    cases: reports,
    reset_source_slots: 16,
    byte_slots: 128,
    scope: "Cold public READ histories and callback rises; shared-reset absolute source epochs.",
  }) + "\n",
);
