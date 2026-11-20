// Independent original-machine observation. This never writes guest RAM.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { serialize, deserialize } from "node:v8";
import { memorySnapshot } from "../validation/reference_memory.mjs";
import { observeVideoClock } from "../validation/reference_timing.mjs";
import { scheduleKeys } from "./reference_inputs.mjs";
import { scheduleAnalogue } from "./reference_analogue.mjs";
import { observeDigitalAudio } from "./reference_audio.mjs";
import { BufferedCapture } from "./reference_capture.mjs";
import { cycleCpu } from "./reference_events.mjs";
import { bitmapFieldHash } from "./reference_frame.mjs";
import { configureDrive } from "./reference_drive.mjs";
import { configureAdcResults } from "./reference_adc_results.mjs";
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const compressedAudio = config.compressed_float_audio ?? false;
if (typeof compressedAudio !== "boolean") throw Error("Invalid float-audio encoding");
const { MachineSession } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
);
const { toSsdOrDsd } = await import(pathToFileURL(path.join(config.jsbeeb, "src/disc.js")));
const { discFor } = await import(pathToFileURL(path.join(config.jsbeeb, "src/fdc.js")));
const session = new MachineSession("B-DFS1.2", { tube: false });
await session.initialise();
const cpu = session._machine.processor;
// The full path identifies the input bytes in the capture receipt. The disc's
// display label must stay stable across independently executed workspaces.
cpu.fdc.loadDisc(
  0,
  discFor(path.basename(config.disc), new Uint8Array(fs.readFileSync(config.disc))),
);
const adcProfile = config.adc_profile ?? "original";
await configureAdcResults(cpu, config.jsbeeb, adcProfile);
const referenceCpu = config.reference_cpu ?? "jsbeeb";
if (!["jsbeeb", "cycle"].includes(referenceCpu)) throw Error("Unknown reference CPU");
const independentCpu = referenceCpu === "cycle" ? cycleCpu(cpu, config.cycle_library) : null;
const driveProfile = config.drive_profile ?? "original-spindle";
configureDrive(cpu.fdc, driveProfile);
const chip = session._soundChip,
  psg = [],
  frames = [],
  fieldHashes = [];
const powerProfile = config.power_profile ?? "original";
if (!["original", "shared-reset"].includes(powerProfile)) throw Error("Unknown power profile");
if (!config.load_state && powerProfile === "shared-reset") {
  // Both machines hold the board in reset for 8 us. Resolve the initial
  // pins and mute the PSG through public device APIs; guest RAM is untouched.
  cpu.currentCycles += 16;
  cpu.targetCycles = cpu.currentCycles;
  cpu.scheduler.polltime(16);
  for (const via of [cpu.sysvia, cpu.uservia]) {
    via.read(13);
    via.reset();
  }
  chip.reset(true);
  for (const byte of [0x9f, 0xbf, 0xdf, 0xff, 0x80]) chip.poke(byte);
  cpu.sysvia.recalculatePortBPins();
  cpu.sysvia.recalculatePortAPins();
  session._video.ula.write(0, 0);
}
const videoClock = observeVideoClock(session._video, chip.scheduler.epoch);
const actualRestarts = [];
if (config.restart_events) {
  if (!independentCpu || config.load_state || config.save_state || config.scenario)
    throw Error("Restart observation requires a cold independent cycle CPU execution");
  const input = fs.readFileSync(config.restart_events, "utf8");
  const lines = input.endsWith("\n") ? input.slice(0, -1).split("\n") : input.split("\n");
  let previous = 0;
  for (const line of lines) {
    if (!/^\d+$/.test(line)) throw Error("Invalid restart event integer");
    const master = Number(line);
    if (
      !Number.isSafeInteger(master) ||
      master < 256 ||
      master >= config.cycles ||
      master % 16 ||
      master <= previous
    )
      throw Error("Invalid restart event time");
    previous = master;
    cpu.scheduler
      .newTask(() => {
        independentCpu.restart();
        actualRestarts.push(cpu.scheduler.epoch * 16);
      })
      .schedule(master / 16 - cpu.scheduler.epoch);
  }
}
const oldPoke = chip.poke.bind(chip);
chip.poke = (value) => {
  psg.push([chip.scheduler.epoch, value]);
  oldPoke(value);
};
const oldPaint = session._video.paint_ext;
session._video.paint_ext = (...args) => {
  oldPaint(...args);
  frames.push(videoClock.cycle);
  fieldHashes.push([videoClock.cycle, bitmapFieldHash(session._completeFb8)]);
};
if (config.load_state) {
  const saved = deserialize(fs.readFileSync(config.load_state));
  if (
    (saved.referenceCpu ?? "jsbeeb") !== referenceCpu ||
    (saved.driveProfile ?? "original-spindle") !== driveProfile ||
    (saved.powerProfile ?? "original") !== powerProfile ||
    (saved.adcProfile ?? "original") !== adcProfile
  )
    throw Error("Own reference snapshot uses a different CPU/drive/power/ADC profile");
  cpu.restoreState(saved.cpu);
  session._fb8.set(saved.frame);
  session._completeFb8.set(saved.complete);
  session._lastPaint = saved.paint;
  if (independentCpu) independentCpu.restore(saved.cycleCpu);
  videoClock.reset(chip.scheduler.epoch);
} else {
  const inputText = fs.readFileSync(config.keys, "utf8").trim();
  const events = inputText
    ? inputText.split("\n").map((line) => line.split(/\s+/).map(Number))
    : [];
  for (const [master, col, row, down] of events) {
    if (
      !Number.isSafeInteger(master / 16) ||
      master < 0 ||
      master >= config.cycles ||
      ![0, 1].includes(down)
    )
      throw Error("Invalid physical boot input");
    cpu.scheduler
      .newTask(() => {
        if (down === 1) session.keyDownRaw([col, row]);
        else session.keyUpRaw([col, row]);
      })
      .schedule(master / 16 - cpu.scheduler.epoch);
  }
  cpu.targetCycles = cpu.currentCycles;
  if (!(await session.runFor(config.cycles / 16 - cpu.currentCycles)).completed)
    throw Error("Reference stopped early");
}
function saveOwnState(file) {
  fs.writeFileSync(
    file,
    serialize({
      referenceCpu,
      driveProfile,
      powerProfile,
      adcProfile,
      cycleCpu: independentCpu?.snapshot(),
      cpu: cpu.snapshotState(),
      frame: session._fb8,
      complete: session._completeFb8,
      paint: session._lastPaint,
    }),
  );
}
if (config.save_state) {
  // Canonical gameplay epoch is a naturally reached complete video field.
  // Drive spin-up/seek latency may differ; no RAM/register state is injected.
  cpu.targetCycles = cpu.currentCycles;
  if (!(await session.runFrames(1)).completed) throw Error("No video boundary after boot");
  chip.catchUp();
  saveOwnState(config.save_state);
}
fs.mkdirSync(config.output, { recursive: true });
if (config.restart_events)
  fs.writeFileSync(path.join(config.output, "restarts.tsv"), actualRestarts.join("\n") + "\n");
if (config.scenario) {
  const { BBC } = await import(pathToFileURL(path.join(config.jsbeeb, "src/keymap.js")));
  const baseline = session.elapsedCycles,
    epoch = chip.scheduler.epoch;
  const bus = [];
  if (config.trace_bus)
    cpu.debugWrite.add((address, data) => {
      if (address >= 0xfe00) {
        const v = session._video;
        bus.push([
          chip.scheduler.epoch - epoch,
          address,
          data,
          v.horizCounter * v.pixelsPerChar,
          v.vertCounter * (v.regs[9] + 1) + v.scanlineCounter,
        ]);
      }
      return false;
    });
  psg.length = 0;
  frames.length = 0;
  fieldHashes.length = 0;
  chip.catchUp();
  chip.position = 0;
  const floats = new BufferedCapture(
    path.join(config.output, compressedAudio ? "audio.f32.gz" : "audio.f32"),
    compressedAudio,
  );
  const digital = new BufferedCapture(path.join(config.output, "audio-digital.u8"));
  const pins = new BufferedCapture(path.join(config.output, "audio-pins.u8"));
  chip._onBuffer = (buffer) => floats.append(Buffer.from(new Float32Array(buffer).buffer));
  observeDigitalAudio(chip, (chunk) => {
    if (chunk.sine) throw Error("External sine generator is outside BBC PSG digital comparison");
    digital.append(chunk.levels);
    pins.append(chunk.pins);
  });
  const timeline = [
    ...config.scenario.events.map((e) => ({ ...e, kind: "input" })),
    ...config.scenario.checkpoints.map((e) => ({ ...e, kind: "checkpoint" })),
    { at_us: config.scenario.duration_us, kind: "end" },
  ].sort((a, b) => a.at_us - b.at_us || (a.kind === "input" ? -1 : 1));
  const actualInputs = scheduleKeys(chip.scheduler, session, BBC, config.scenario.events);
  const actualAnalogue = scheduleAnalogue(
    chip.scheduler,
    cpu,
    config.scenario.analogue ?? [],
    config.scenario.duration_us,
  );
  const actualCheckpoints = [];
  for (const event of timeline) {
    const remaining = baseline + event.at_us * 2 - session.elapsedCycles;
    cpu.targetCycles = cpu.currentCycles;
    if (remaining > 0 && !(await session.runFor(remaining)).completed)
      throw Error("Reference stopped early");
    const cycle = session.elapsedCycles - baseline;
    if (cycle < event.at_us * 2 || cycle - event.at_us * 2 > 32)
      throw Error("Instruction timing bound exceeded");
    if (event.kind === "checkpoint") {
      fs.writeFileSync(
        path.join(config.output, event.name + ".ssd"),
        toSsdOrDsd(cpu.fdc._drives[0].disc),
      );
      fs.writeFileSync(path.join(config.output, event.name + ".ram"), memorySnapshot(cpu));
      fs.writeFileSync(path.join(config.output, event.name + ".rgba"), session._completeFb8);
      actualCheckpoints.push({ name: event.name, cycle });
    }
  }
  chip.catchUp();
  floats.append(Buffer.from(chip.buffer.slice(0, chip.position).buffer));
  for (const capture of [floats, digital, pins]) capture.close();
  fs.writeFileSync(
    path.join(config.output, "disc-output.ssd"),
    toSsdOrDsd(cpu.fdc._drives[0].disc),
  );
  fs.writeFileSync(
    path.join(config.output, "psg.tsv"),
    psg.map(([c, d]) => [c - epoch, d].join("\t")).join("\n") + (psg.length ? "\n" : ""),
  );
  fs.writeFileSync(
    path.join(config.output, "frames.tsv"),
    frames.map((c) => c - baseline).join("\n") + "\n",
  );
  fs.writeFileSync(
    path.join(config.output, "field-hashes.tsv"),
    fieldHashes.map(([c, h]) => `${c - baseline} ${h}`).join("\n") + "\n",
  );
  fs.writeFileSync(
    path.join(config.output, "bus.tsv"),
    bus.map((row) => row.join("\t")).join("\n") + "\n",
  );
  fs.writeFileSync(path.join(config.output, "analogue.json"), JSON.stringify(actualAnalogue));
  fs.writeFileSync(path.join(config.output, "inputs.json"), JSON.stringify(actualInputs) + "\n");
  fs.writeFileSync(
    path.join(config.output, "summary.json"),
    JSON.stringify({
      completed: true,
      audio: {
        float_encoding: compressedAudio ? "gzip-members" : "raw",
        float_bytes: floats.bytes,
      },
      baseline_cycles: baseline,
      duration_cycles: session.elapsedCycles - baseline,
      clock_hz: 2000000,
      frames: frames.length,
      checkpoints: actualCheckpoints,
      memory_capture: "non-mutating peekmem",
    }) + "\n",
  );
  if (config.save_scenario_state) {
    // A continuation begins at an actually completed field on this machine.
    // Reject an arbitrary endpoint rather than inventing pixels or advancing
    // one backend to a different game/input epoch.
    if (frames.at(-1) !== epoch + session.elapsedCycles - baseline)
      throw Error("Own gameplay checkpoint must end at a complete video field");
    saveOwnState(config.save_scenario_state);
  }
  process.exit(0);
}
fs.writeFileSync(path.join(config.output, "boot.ram"), memorySnapshot(session._machine.processor));
fs.writeFileSync(path.join(config.output, "boot.rgba"), session._completeFb8);
fs.writeFileSync(
  path.join(config.output, "psg.tsv"),
  psg.map((row) => row.join("\t")).join("\n") + (psg.length ? "\n" : ""),
);
fs.writeFileSync(path.join(config.output, "frames.tsv"), frames.join("\n") + "\n");
fs.writeFileSync(
  path.join(config.output, "field-hashes.tsv"),
  fieldHashes.map((row) => row.join(" ")).join("\n") + "\n",
);
const v = session._video;
fs.writeFileSync(
  path.join(config.output, "video.json"),
  JSON.stringify({
    control: v.ulactrl,
    registers: Array.from(v.regs.slice(0, 18)),
    palette: Array.from(v.actualPal),
    frames: frames.length,
  }) + "\n",
);
fs.writeFileSync(
  path.join(config.output, "summary.json"),
  JSON.stringify({
    cycles: session.elapsedCycles,
    registers: session.registers(),
    vdu: session.drainOutput(),
    memory_capture: "non-mutating peekmem; MMIO locations are FF",
  }) + "\n",
);
