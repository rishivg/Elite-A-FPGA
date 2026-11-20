// Private jsbeeb capture hooks are deliberately tied to reference.lock.json.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
import { bootReleased } from "./reference_boot.mjs";
import { memorySnapshot } from "./reference_memory.mjs";
const { BBC } = await import(pathToFileURL(path.join(config.jsbeeb, "src/keymap.js")));
const session = await bootReleased(config);
const baseline = session.elapsedCycles,
  epoch = session._soundChip.scheduler.epoch;
const out = config.output;
fs.mkdirSync(out, { recursive: true });
const chip = session._soundChip;
chip.catchUp();
chip.position = 0;
const chunks = [],
  writes = [],
  frames = [],
  inputs = [],
  checkpoints = [];
chip._onBuffer = (buffer) => chunks.push(Buffer.from(new Float32Array(buffer).buffer));
const oldPoke = chip.poke.bind(chip);
chip.poke = (value) => {
  writes.push([chip.scheduler.epoch - epoch, value]);
  oldPoke(value);
};
const oldPaint = session._video.paint_ext;
session._video.paint_ext = (...args) => {
  oldPaint(...args);
  frames.push(session.elapsedCycles - baseline);
};
const timeline = [
  ...config.events.map((x) => ({ ...x, kind: "input" })),
  ...config.checkpoints.map((x) => ({ ...x, kind: "checkpoint" })),
  { at_us: config.duration_us, kind: "end" },
].sort((a, b) => a.at_us - b.at_us || (a.kind === "input" ? -1 : 1));
for (const event of timeline) {
  const target = baseline + event.at_us * 2;
  const remaining = target - session.elapsedCycles;
  if (remaining > 0) {
    // jsbeeb accumulates requested budgets across calls. Discard the
    // previous instruction's unused budget so each absolute event target
    // refers to actual elapsed guest cycles, not accumulated requests.
    session._machine.processor.targetCycles = session._machine.processor.currentCycles;
    const result = await session.runFor(remaining);
    if (!result.completed) throw Error("Reference execution stopped early");
  }
  const cycle = session.elapsedCycles - baseline;
  // Instructions finish atomically; retain actual timestamps and bound overshoot.
  if (cycle < event.at_us * 2 || cycle - event.at_us * 2 > 32)
    throw Error("Reference checkpoint timing exceeded instruction bound");
  if (event.kind === "input") {
    session.releaseAllKeys();
    for (const key of event.keys) {
      if (!BBC[key]) throw Error(`Unknown BBC key ${key}`);
      session.keyDownRaw(BBC[key]);
    }
    inputs.push({ requested_cycle: event.at_us * 2, cycle, keys: event.keys });
  } else if (event.kind === "checkpoint") {
    fs.writeFileSync(
      path.join(out, event.name + ".ram"),
      memorySnapshot(session._machine.processor),
    );
    fs.writeFileSync(path.join(out, event.name + ".rgba"), session._completeFb8);
    checkpoints.push({
      name: event.name,
      requested_cycle: event.at_us * 2,
      cycle,
      paint_bounds: session._lastPaint,
      psg: chip.getState(),
      registers: session.registers(),
    });
  }
}
chip.catchUp();
chunks.push(Buffer.from(chip.buffer.slice(0, chip.position).buffer));
fs.writeFileSync(path.join(out, "audio.f32"), Buffer.concat(chunks));
for (const [name, items] of [
  ["psg", writes],
  ["frames", frames.map((c) => [c])],
])
  fs.writeFileSync(
    path.join(out, name + ".tsv"),
    items.map((x) => x.join("\t")).join("\n") + (items.length ? "\n" : ""),
  );
fs.writeFileSync(path.join(out, "inputs.json"), JSON.stringify(inputs, null, 2) + "\n");
fs.writeFileSync(
  path.join(out, "summary.json"),
  JSON.stringify(
    {
      completed: true,
      cpu_hz: 2000000,
      baseline_cycles: baseline,
      duration_cycles: session.elapsedCycles - baseline,
      frames: frames.length,
      checkpoints,
      audio_samples: chunks.reduce((n, b) => n + b.length / 4, 0),
      vdu_text: session.drainOutput(),
    },
    null,
    2,
  ) + "\n",
);
if (frames.length < 2) throw Error("Reference did not produce video frames");
