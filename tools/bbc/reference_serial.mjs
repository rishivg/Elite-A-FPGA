// Closed Model B fixture: no cassette image and no attached RS-423 handler.
// Unchanged MC6850, serial ULA and scheduler execute every register operation.
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, output] = process.argv.slice(2);
const { Acia } = await import(pathToFileURL(root + "/src/acia.js"));
const { Serial } = await import(pathToFileURL(root + "/src/serial.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const rows = [];
let cases = 0,
  observations = 0,
  staticCases = 0,
  dynamicCases = 0;

function capture(events, times) {
  const scheduler = new Scheduler();
  const cpu = { model: { cyclesPerSecond: 2000000 }, interrupt: 0 };
  const acia = new Acia(cpu, { mute() {}, tone() {} }, scheduler, { motorOn() {}, motorOff() {} });
  const serial = new Serial(acia);
  acia.reset();
  const ordered = [...new Set(times)].sort((a, b) => a - b);
  rows.push(`case ${cases++} ${ordered.length}`);
  let current = 0;
  for (const cycle of ordered) {
    scheduler.polltime(cycle - current);
    current = cycle;
    const [action, data] = events.get(cycle) ?? [0, 0];
    if (action === 1) acia.write(0, data);
    if (action === 2) acia.write(1, data);
    if (action === 3) serial.write(0, data);
    if (action === 4) serial.read(0);
    if (action === 5) acia.read(1);
    rows.push(
      [
        cycle,
        action,
        data,
        acia.read(0),
        +!!(cpu.interrupt & 4),
        acia.serialTransmitCyclesPerByte,
        acia.cr,
        serial.transmitRate,
        +acia.rs423Selected,
        +!!(acia.sr & 2),
        +acia.txCompleteTask.scheduled(),
        acia.txCompleteTask.scheduled() ? acia.txCompleteTask.expireEpoch - scheduler.epoch : 0,
      ].join(" "),
    );
    observations++;
  }
}

// All control bytes, prior control/reset retention, eight baud selectors and
// both channel selections. These also check deadlines without waiting for them.
for (const previous of [0, 0x14, 0x20])
  for (let command = 0; command < 256; command++)
    for (let baud = 0; baud < 8; baud++)
      for (const rs423 of [0, 1]) {
        const events = new Map([
          [1, [1, previous]],
          [2, [3, baud | (rs423 << 6)]],
          [3, [1, command]],
          [4, [2, 0xa5]],
          [5, [5, 0]],
          [6, [4, 0]],
          [7, [3, ((baud + 3) & 7) | ((rs423 ^ 1) << 6)]],
          [8, [1, 3]],
          [9, [1, command ^ 0x20]],
          [10, [2, 0x5a]],
        ]);
        capture(events, [0, ...events.keys()]);
        staticCases++;
      }

// Every framing/baud/channel combination, preserving all physical master
// edges in the FPGA probe while the original scheduler advances independently.
for (let format = 0; format < 8; format++)
  for (let baud = 0; baud < 8; baud++)
    for (const rs423 of [0, 1])
      for (const collision of [
        "plain",
        "reset-before",
        "restart-before",
        "restart-at",
        "rate-before",
      ]) {
        const command = (format << 2) | 0x21;
        // Ask the original public API for its deadline; no RTL table is used.
        const scheduler = new Scheduler();
        const acia = new Acia(
          { model: { cyclesPerSecond: 2000000 }, interrupt: 0 },
          { mute() {}, tone() {} },
          scheduler,
          { motorOn() {}, motorOff() {} },
        );
        const serial = new Serial(acia);
        serial.write(0, baud | (rs423 << 6));
        acia.write(0, command);
        const period = acia.serialTransmitCyclesPerByte,
          deadline = 3 + period;
        const events = new Map([
          [1, [3, baud | (rs423 << 6)]],
          [2, [1, command]],
          [3, [2, 0xa5]],
        ]);
        let final = deadline;
        if (collision === "reset-before") events.set(deadline - 1, [1, 3]);
        if (collision === "restart-before" || collision === "restart-at") {
          const restart = deadline - (collision === "restart-before" ? 1 : 0);
          events.set(restart, [2, 0x5a]);
          final = restart + period;
        }
        if (collision === "rate-before") {
          const next = ((baud + 1) & 7) | ((rs423 ^ 1) << 6);
          events.set(deadline - 1, [3, next]);
          events.set(deadline + 2, [2, 0x5a]);
          serial.write(0, next);
          final = deadline + 2 + acia.serialTransmitCyclesPerByte;
        }
        const times = [
          0,
          1,
          2,
          3,
          4,
          deadline - 2,
          deadline - 1,
          deadline,
          deadline + 1,
          deadline + 2,
          final - 1,
          final,
          final + 1,
        ];
        capture(events, times);
        dynamicCases++;
      }
rows.push(`total ${cases} ${observations}`);
fs.writeFileSync(output, rows.join("\n") + "\n");
console.log(
  JSON.stringify({ cases, observations, static_cases: staticCases, dynamic_cases: dynamicCases }),
);
