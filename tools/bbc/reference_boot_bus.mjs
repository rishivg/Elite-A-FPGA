// Passive accepted-write observer for an independently executed original boot.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { BufferedCapture } from "./reference_capture.mjs";
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (config.power_profile !== "shared-reset" || config.load_state || config.scenario)
  throw Error("Cold bus observation requires independent shared-reset execution");
const { MachineSession } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
);
const { Video } = await import(pathToFileURL(path.join(config.jsbeeb, "src/video.js")));
fs.mkdirSync(config.output, { recursive: true });
const output = new BufferedCapture(path.join(config.output, "cold-mmio.tsv"));
let cycle = 16,
  writes = 0,
  ready = false;
const initialise = MachineSession.prototype.initialise;
MachineSession.prototype.initialise = async function (...args) {
  const result = await initialise.apply(this, args);
  if (ready) throw Error("Multiple machines in cold bus observation");
  const cpu = this._machine.processor;
  if (cpu.scheduler.epoch !== 0) throw Error("Unexpected original startup epoch");
  ready = true;
  const write = cpu.writemem;
  cpu.writemem = function (address, data) {
    const result = write.call(this, address, data);
    if (address >= 0xfe00) {
      output.append(Buffer.from(`${cycle} ${address} ${data}\n`));
      writes++;
    }
    return result;
  };
  return result;
};
const poll = Video.prototype.polltime;
Video.prototype.polltime = function (count) {
  const result = poll.call(this, count);
  if (ready) cycle += count;
  return result;
};
process.on("exit", () => {
  output.close();
  fs.writeFileSync(
    path.join(config.output, "cold-mmio.json"),
    JSON.stringify({ initial_cycle: 16, final_cycle: cycle, writes, ready }),
  );
});
