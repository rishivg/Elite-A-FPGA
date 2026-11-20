// Exercise original slot parity and ULA writes after the slot has completed.
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const video = new Video(false, new Uint32Array(1024 * 625), () => {});
video.sysvia = { setVBlankInt() {} };
video.ula.write(0, 0);
if (video.oddClock !== false || video.halfClock !== true)
  throw Error("Changed declared source clock initialization");
let cases = 0;
for (let odd = 0; odd < 2; ++odd)
  for (let fast = 0; fast < 2; ++fast)
    for (let address = 0; address < 2; ++address)
      for (let data = 0; data < 256; ++data) {
        video.ula.write(0, fast ? 16 : 0);
        video.oddClock = !!odd;
        video.horizCounter = 3;
        video.regs[0] = 255;
        video.inHSync = false;
        video.bitmapX = 0;
        video.bitmapY = 0;
        video.polltime(1);
        const advanced = fast || !odd;
        if (video.horizCounter !== 3 + Number(advanced) || video.oddClock !== !odd)
          throw Error("Source character acceptance differs from original parity");
        video.ula.write(address, data);
        const control = address ? (fast ? 16 : 0) : data;
        if (video.ulactrl !== control || video.halfClock !== !(control & 16))
          throw Error("Source ULA completion ordering differs");
        cases++;
      }
if (cases !== 2048) throw Error("Incomplete source clock inventory");
fs.writeFileSync(output, JSON.stringify({ passed: true, cases }) + "\n");
