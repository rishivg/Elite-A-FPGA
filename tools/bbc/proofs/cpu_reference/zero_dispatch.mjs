// Public original command histories; NMI is observed at each accepted bus edge.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, driveHelper, discPath, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const { discFor } = await import(pathToFileURL(root + "/src/fdc.js"));
const { configureDrive } = await import(pathToFileURL(driveHelper));
const profiles = [];
for (const [name, command, parameters] of [
  ["read", 0x53, [0, 0, 0x21]],
  ["write", 0x4b, [0, 0, 0x21]],
  ["write-deleted", 0x4f, [0, 0, 0x21]],
  ["verify", 0x5f, [0, 0, 0x21]],
  ["read-id", 0x5b, [0, 0, 1]],
  ["scan", 0x40, [0, 0, 0x21, 1, 0]],
  ["scan-deleted", 0x44, [0, 0, 0x21, 1, 0]],
  ["seek", 0x69, [0]],
])
  profiles.push({ name: "cold-" + name, command, parameters });
profiles.push({ name: "drive-one", command: 0xa9, parameters: [0] });
profiles.push({ name: "retained-load", command: 0x69, parameters: [0], head: 0xf0 });
profiles.push({ name: "step-one", command: 0x69, parameters: [1], head: 0xf0, step: 1 });
profiles.push({ name: "step-three", command: 0x69, parameters: [3], head: 0xf0, step: 1 });
profiles.push({ name: "positive-head-wait", command: 0x53, parameters: [0, 0, 0x21], head: 1 });
profiles.push({ name: "ready-no-step", ready: true, command: 0x69, parameters: [0], head: 0xf0 });
profiles.push({ name: "repeat-zero", command: 0x69, parameters: [0], repeats: 8 });
profiles.push({
  name: "same-track",
  command: 0x69,
  parameters: [43],
  head: 0xf0,
  registers: [
    [0x10, 255],
    [0x11, 255],
    [0x12, 43],
  ],
});
profiles.push({
  name: "bad-track-wrap",
  command: 0x69,
  parameters: [254],
  head: 0xf0,
  registers: [
    [0x10, 254],
    [0x11, 255],
  ],
});
profiles.push({
  name: "single-actuator",
  command: 0x69,
  parameters: [0],
  head: 0xf0,
  registers: [
    [0x17, 2],
    [0x1a, 9],
  ],
});
const lines = [],
  reports = [];
for (let id = 0; id < profiles.length; id++) {
  const profile = profiles[id],
    scheduler = new Scheduler();
  let nmi = false,
    edges = 0;
  const fdc = new IntelFdc(
    {
      setNmi(_source, level) {
        if (level && !nmi) edges++;
        nmi = level;
      },
      nmiAsserted: () => nmi,
    },
    scheduler,
  );
  if (profile.ready)
    fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(discPath))));
  if (profile.ready) configureDrive(fdc, "clocked-fm");
  const rows = [],
    completions = [];
  function record(action, address, data, advance = 1) {
    scheduler.polltime(advance);
    let returned = 0;
    if (action === "W") fdc.write(address, data);
    else if (action === "R") returned = fdc.read(address);
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
  function command(op, ...params) {
    record("W", 0, op);
    for (const p of params) record("W", 1, p);
  }
  if (profile.head !== undefined || profile.step !== undefined)
    command(0x35, 0x0d, profile.step ?? 0, 0, profile.head ?? 0);
  if (profile.step) command(0x35, 0x10, 255, 255, 0);
  for (const [address, data] of profile.registers ?? []) command(0x3a, address, data);
  if (profile.ready) {
    command(0x3a, 0x23, 0x48);
    record("P", 0, 0, 850000);
    command(0x6c);
    record("R", 1, 0);
    command(0x6c);
    if (!(record("R", 1, 0) & 4)) throw Error("Original drive did not become ready");
  }
  for (let repeat = 0; repeat < (profile.repeats ?? 1); repeat++) {
    const previous = edges;
    command(profile.command, ...profile.parameters);
    const started = scheduler.epoch;
    let before = null;
    while (!nmi && scheduler.epoch - started < 100000) {
      before = [scheduler.epoch, "P", 0, fdc.read(0), fdc.read(0), +nmi, edges].join(" ");
      scheduler.polltime(1);
    }
    if (!nmi || edges !== previous + 1)
      throw Error("Original command did not complete exactly once");
    if (scheduler.epoch > started) {
      if (before && +before.split(" ")[0] > started) rows.push(before);
      rows.push([scheduler.epoch, "P", 0, fdc.read(0), fdc.read(0), +nmi, edges].join(" "));
    }
    completions.push({
      started,
      completed: scheduler.epoch,
      immediate: scheduler.epoch === started,
    });
    const result = record("R", 1, 0);
    if (result !== (profile.ready ? 0 : 0x10)) throw Error("Original completion result changed");
  }
  const registers = [];
  for (const address of [0x12, 0x1a, 0x15, 0x1b, 0x23, 0x02]) {
    command(0x7d, address);
    registers.push([address, record("R", 1, 0)]);
  }
  record("P", 0, 0);
  lines.push(`case ${id} ${profile.name} ${rows.length}`, ...rows);
  reports.push({ ...profile, rows: rows.length, completions, registers, edges });
}
fs.writeFileSync(directory + "/original.tsv", lines.join("\n") + "\n");
fs.writeFileSync(
  directory + "/original.json",
  JSON.stringify({
    passed: true,
    cases: reports,
    scope: "Cold public commands and accepted-edge NMI counts; no DUT state injection.",
  }) + "\n",
);
