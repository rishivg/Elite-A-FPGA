// Public Original CRTC writes and light-pen callback, with independent address fixtures.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const video = new Video(false, new Uint32Array(1024 * 625), () => {});
const masks = [];
let observations = 0;
for (let index = 0; index < 16; index++) {
  video.crtc.write(0, index);
  video.crtc.write(1, 255);
  const mask = video.regs[index];
  masks.push(mask);
  for (let data = 0; data < 256; data++) {
    video.crtc.write(1, data);
    if (video.regs[index] !== (data & mask)) throw Error(`Write mask mismatch ${index}/${data}`);
    observations++;
  }
}
let retained = 0;
for (let address = 0; address < 16384; address++)
  for (let level = 0; level < 2; level++)
    for (let output = 0; output < 2; output++) {
      // This address belongs only to the independent reference fixture.
      video.addr = address;
      video.cb2changed(!!level, !!output);
      if (level && output) retained = address;
      if (((video.regs[16] << 8) | video.regs[17]) !== retained)
        throw Error(`Light pen mismatch ${address}/${level}/${output}`);
      observations++;
    }
fs.writeFileSync(output, JSON.stringify({ passed: true, observations, masks }) + "\n");
