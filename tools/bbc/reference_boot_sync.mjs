// Observe raw VSYNC rather than the frontend's minimum-height paint filter.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { observeDigitalAudio } from "./reference_audio.mjs";
import { BufferedCapture } from "./reference_capture.mjs";

const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (config.power_profile !== "shared-reset" || config.load_state || config.scenario)
  throw Error("Raw boot sync observation requires an independently executed shared-reset boot");
const { Video } = await import(pathToFileURL(path.join(config.jsbeeb, "src/video.js")));
const { SoundChip } = await import(pathToFileURL(path.join(config.jsbeeb, "src/soundchip.js")));
fs.mkdirSync(config.output, { recursive: true });
const audio = new BufferedCapture(path.join(config.output, "audio.u8.gz"), true);
let observedAudio = false,
  observedChip;
const resetSound = SoundChip.prototype.reset;
SoundChip.prototype.reset = function (hard) {
  resetSound.call(this, hard);
  // The explicit shared reset ends after sixteen 2 MHz intervals. Observe
  // unchanged generators after that reset and before guest execution.
  if (hard && this.scheduler.epoch === 16 && !observedAudio) {
    observedAudio = true;
    observedChip = this;
    observeDigitalAudio(this, ({ levels, sine }) => {
      if (sine) throw Error("Boot waveform contains a non-PSG sine source");
      audio.append(Buffer.from(levels));
    });
  }
};
const poll = Video.prototype.polltime;
let cycle = 16,
  previous = false;
const transitions = [];
Video.prototype.polltime = function (count) {
  if (!Number.isInteger(count) || count < 0) throw Error("Invalid original video clock batch");
  for (let i = 0; i < count; i++) {
    cycle++;
    poll.call(this, 1);
    const level = !!this.inVSync;
    if (level !== previous) {
      transitions.push({ cycle, level, control: this.ulactrl });
      previous = level;
    }
  }
};
process.on("exit", () => {
  // Flush the original generators to their own scheduler's final boundary.
  // Long saved boots already do this through the normal reference runner;
  // short reset regressions also need their last digital interval observed.
  observedChip?.catchUp();
  audio.close();
  if (fs.existsSync(config.output))
    fs.writeFileSync(
      path.join(config.output, "raw-vsync.json"),
      JSON.stringify({
        initial_cycle: 16,
        final_cycle: cycle,
        transitions,
        observed_audio: observedAudio,
      }),
    );
});
