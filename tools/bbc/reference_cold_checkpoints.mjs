// Passive RAM observations during an independently executed cold game history.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { memorySnapshot } from "../validation/reference_memory.mjs";

const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (
  config.power_profile !== "shared-reset" ||
  config.load_state ||
  config.scenario ||
  config.save_state
)
  throw Error("Cold checkpoints require independent execution without saved state");
const points = config.cold_checkpoints;
if (!Array.isArray(points) || points.length === 0) throw Error("Missing cold checkpoints");
let previous = 256;
const names = new Set();
for (const point of points) {
  if (
    !Number.isSafeInteger(point.cycle) ||
    point.cycle <= previous ||
    point.cycle > config.cycles ||
    point.cycle % 16 !== 0 ||
    typeof point.name !== "string" ||
    !/^[a-z][a-z0-9_]*$/.test(point.name) ||
    names.has(point.name)
  )
    throw Error("Invalid cold checkpoint schedule");
  previous = point.cycle;
  names.add(point.name);
}
const { MachineSession } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
);
const initialise = MachineSession.prototype.initialise;
let ready = false;
const observed = [];
MachineSession.prototype.initialise = async function (...args) {
  const result = await initialise.apply(this, args);
  if (ready) throw Error("Multiple machines in cold checkpoints");
  const cpu = this._machine.processor;
  if (cpu.scheduler.epoch !== 0) throw Error("Unexpected initial checkpoint epoch");
  ready = true;
  for (const point of points) {
    cpu.scheduler
      .newTask(() => {
        const cycle = cpu.scheduler.epoch * 16;
        if (cycle !== point.cycle) throw Error("Checkpoint did not execute on its requested clock");
        fs.mkdirSync(config.output, { recursive: true });
        fs.writeFileSync(path.join(config.output, point.name + ".ram"), memorySnapshot(cpu));
        observed.push({ cycle, name: point.name });
      })
      .schedule(point.cycle / 16);
  }
  return result;
};
process.on("exit", () => {
  fs.mkdirSync(config.output, { recursive: true });
  fs.writeFileSync(
    path.join(config.output, "cold-checkpoints.json"),
    JSON.stringify({ ready, requested: points, observed }),
  );
});
