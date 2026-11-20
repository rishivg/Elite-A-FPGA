// Record original-machine video transactions for a separate original renderer.
// Wrappers call the original APIs first; no guest or device state is changed.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { memorySnapshot } from "../validation/reference_memory.mjs";
import { BufferedCapture } from "./reference_capture.mjs";

const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (config.power_profile !== "shared-reset" || config.load_state || config.scenario)
  throw Error("Video trace requires an independently executed shared-reset boot");
const { MachineSession } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
);
const { Video } = await import(pathToFileURL(path.join(config.jsbeeb, "src/video.js")));
fs.mkdirSync(config.output, { recursive: true });
const events = new BufferedCapture(path.join(config.output, "video-transactions.tsv"));
let cycle = 16,
  processor,
  initial,
  count = 0;
function record(kind, address, data) {
  if (!Number.isSafeInteger(cycle) || cycle < 16) throw Error("Invalid video bus clock");
  events.append(Buffer.from(`${cycle} ${kind} ${address} ${data}\n`));
  count++;
}
const initialise = MachineSession.prototype.initialise;
MachineSession.prototype.initialise = async function (...args) {
  const result = await initialise.apply(this, args);
  if (processor) throw Error("Multiple original machines in a boot trace");
  processor = this._machine.processor;
  const video = this._video;
  initial = {
    registers: [...video.regs],
    control: video.ulactrl,
    screen_subtract: video.screenSubtract,
    epoch: processor.scheduler.epoch,
  };
  if (initial.epoch !== 0 || initial.registers.some((r) => r !== 0))
    throw Error("Original initial video profile differs from the declared zero-register reset");
  fs.writeFileSync(
    path.join(config.output, "video-initial.ram"),
    memorySnapshot(processor, 0, 32768),
  );
  const write = processor.writemem;
  processor.writemem = function (address, data) {
    const result = write.call(this, address, data);
    if (address < 32768) record("ram", address, data);
    return result;
  };
  for (const [device, kind] of [
    [video.crtc, "crtc"],
    [video.ula, "ula"],
  ]) {
    const write = device.write;
    device.write = function (address, data) {
      const result = write.call(this, address, data);
      record(kind, address, data);
      return result;
    };
  }
  const scroll = video.setScreenHwScroll;
  video.setScreenHwScroll = function (value) {
    const result = scroll.call(this, value);
    record("scroll", value, 0);
    return result;
  };
  return result;
};
const poll = Video.prototype.polltime;
Video.prototype.polltime = function (count) {
  const result = poll.call(this, count);
  if (processor) cycle += count;
  return result;
};
process.on("exit", () => {
  events.close();
  if (!processor) return;
  fs.writeFileSync(
    path.join(config.output, "video-final.ram"),
    memorySnapshot(processor, 0, 32768),
  );
  fs.writeFileSync(
    path.join(config.output, "video-transactions.json"),
    JSON.stringify({ initial, initial_cycle: 16, final_cycle: cycle, transactions: count }),
  );
});
