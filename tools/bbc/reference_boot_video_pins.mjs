// Read original video fetches and cursor control without changing guest state.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { BufferedCapture } from "./reference_capture.mjs";

const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (config.power_profile !== "shared-reset" || config.load_state || config.scenario)
  throw Error("Video pins require an independently executed shared-reset boot");
const { MachineSession } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/machine-session.js"))
);
const { Video, VDISPENABLE } = await import(
  pathToFileURL(path.join(config.jsbeeb, "src/video.js"))
);
fs.mkdirSync(config.output, { recursive: true });
const output = new BufferedCapture(path.join(config.output, "video-pins.bin.gz"), true);
let ready = false,
  cycle = 16,
  line = 0,
  count = 0,
  teletextSlots = 0,
  fetches = 0,
  cursor = 0,
  observingRead = false,
  memoryAddress;
const initialise = MachineSession.prototype.initialise;
MachineSession.prototype.initialise = async function (...args) {
  const result = await initialise.apply(this, args);
  if (ready) throw Error("Multiple original machines in a video-pin trace");
  const processor = this._machine.processor;
  const read = processor.videoRead;
  processor.videoRead = function (address) {
    if (observingRead) memoryAddress = address;
    return read.call(this, address);
  };
  ready = true;
  return result;
};
const endScanline = Video.prototype.endOfScanline;
Video.prototype.endOfScanline = function (...args) {
  line = (line + 1) & 1023;
  return endScanline.apply(this, args);
};
const endFrame = Video.prototype.endOfFrame;
Video.prototype.endOfFrame = function (...args) {
  line = 0;
  return endFrame.apply(this, args);
};
const handleCursor = Video.prototype.handleCursor;
Video.prototype.handleCursor = function (...args) {
  cursor = +!!(this.cursorOnThisFrame && this.ulactrl & this.cursorTable[this.cursorDrawIndex]);
  return handleCursor.apply(this, args);
};
const poll = Video.prototype.polltime;
Video.prototype.polltime = function (clocks) {
  if (!Number.isInteger(clocks) || clocks < 0) throw Error("Invalid original video clock batch");
  for (let i = 0; i < clocks; i++) {
    let packet;
    cursor = 0;
    if (ready) {
      // Fetch inputs precede this slot's CRTC endpoint and CPU bus write.
      // The original readVideoMem / videoRead APIs are pure RAM observations.
      packet = Buffer.alloc(20);
      packet.writeUInt32LE(cycle, 0);
      packet.writeUInt16LE(this.horizCounter, 4);
      packet.writeUInt16LE(this.addr, 6);
      packet.writeUInt16LE(line, 8);
      packet[10] = (this.scanlineCounter & 30) | +!!this.teletext.levelRA0;
      packet[11] =
        +!!(this.dispEnabled & VDISPENABLE) |
        (+this.inVSync << 1) |
        (+this.inHSync << 2) |
        (+!!(this.cursorOn && !this.cursorOff) << 3) |
        (+!!this.cursorOnThisFrame << 4) |
        (+!this.doEvenFrameLogic << 5) |
        (+this.oddClock << 6);
      packet[12] = this.ulactrl;
      packet[13] = this.regs[1];
      packet[14] = this.regs[8];
      teletextSlots += +(!!(this.ulactrl & 2) && !(this.ulactrl & 16));
      fetches += +(!!(this.ulactrl & 16) || !this.oddClock);
      observingRead = true;
      memoryAddress = undefined;
      try {
        packet[15] = this.readVideoMem();
      } finally {
        observingRead = false;
      }
      if (!Number.isInteger(memoryAddress) || memoryAddress < 0 || memoryAddress >= 32768)
        throw Error("Original video fetch did not observe one RAM address");
      packet.writeUInt16LE(memoryAddress, 16);
    }
    poll.call(this, 1);
    if (ready) {
      packet[11] |= cursor << 7;
      packet[18] = +this.inVSync | (+this.inHSync << 1);
      output.append(packet);
      count++;
      cycle++;
    }
  }
};
process.on("exit", () => {
  output.close();
  fs.writeFileSync(
    path.join(config.output, "video-pins.json"),
    JSON.stringify({
      count,
      initial_cycle: 16,
      final_cycle: cycle,
      record_bytes: 20,
      uncompressed_bytes: output.bytes,
      teletext_slots: teletextSlots,
      video_fetches: fetches,
      format:
        "Before-slot CRTC/RAM inputs; observed original cursor control and after-slot HS/VS. Concatenated gzip members.",
    }),
  );
});
