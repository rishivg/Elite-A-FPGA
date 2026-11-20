// Original Video/ULA; caches contain only this original model's own warm state.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out, variant] = process.argv.slice(2);
if (variant !== undefined && variant !== "mode" && variant !== "clock")
  throw Error("Unknown ULA fixture variant");
const { Video, EVERYTHINGENABLED } = await import(pathToFileURL(root + "/src/video.js"));
fs.mkdirSync(out, { recursive: true });
const periods = [];
const regs = [63, 32, 45, 36, 38, 0, 31, 34, 0, 7, 32, 8, 12, 0, 13, 130, 0, 0];
for (const fast of [0, 1])
  for (const mode of [0, 1, 2, 3]) {
    const crtc = regs.slice();
    if (fast) {
      crtc[0] = 127;
      crtc[1] = 64;
      crtc[2] = 90;
      crtc[3] = 40;
    }
    const fb = new Uint32Array(1024 * 625);
    let paints = 0,
      latest,
      inputByte = 0;
    const video = new Video(false, fb, () => {
      paints++;
      latest = new Uint32Array(fb);
    });
    video.reset(
      { videoRead: (address) => (inputByte === 256 ? (address >>> 3) & 255 : inputByte) },
      { setVBlankInt() {} },
    );
    // Make the initial ULA mode explicit through its bus. The original
    // emulator constructor's halfClock default is not its control=0 mode.
    video.polltime(1);
    video.ula.write(0, 0);
    for (let a = 0; a < 18; a++) {
      video.polltime(1);
      video.crtc.write(0, a);
      video.polltime(1);
      video.crtc.write(1, crtc[a]);
    }
    video.polltime(1);
    video.ula.write(0, (fast ? 16 : 0) | (mode << 2));
    for (let i = 0; i < 16; i++) {
      video.polltime(1);
      video.ula.write(1, i << 4);
    }
    while (paints < 5) video.polltime(1);
    const baseline = video.snapshotState();
    // Observe the original renderer's memory reads. Clock-switch tests compare
    // emitted digital dots, rather than a CRT crop whose origin moves with HS.
    const readVideoMem = video.readVideoMem;
    let fetched;
    video.readVideoMem = function () {
      const dat = readVideoMem.call(this);
      if ((this.dispEnabled & EVERYTHINGENABLED) === EVERYTHINGENABLED) fetched = dat;
      return dat;
    };
    const dots = new Uint32Array(16);
    const renderer = {
      fb32: dots,
      table4bpp: video.table4bpp,
      pixelColours: () => video.pixelColours(),
    };
    for (const byte of [0, 85, 170, 255, 256])
      for (const event of [-1, 29697, 29698, 29699, 29700]) {
        const id = `${byte}-${fast}-${mode}-${event}`;
        inputByte = byte;
        video.restoreState(baseline);
        fb.fill(0xff000000);
        paints = 5;
        let elapsed = 0;
        let cell = baseline.cellData,
          displayed = false;
        const pins = [];
        const sync = [];
        do {
          fetched = undefined;
          video.polltime(1);
          elapsed++;
          if (variant === "clock") {
            sync.push(+video.inHSync | (+video.inVSync << 1));
            if (!video.halfClock || video.oddClock) {
              displayed = fetched !== undefined;
              cell = fetched;
            }
            dots.fill(0xff000000);
            if (displayed) {
              renderer.ulaMode = video.ulaMode;
              // Execute the unchanged original ULA decoder and palette. Its
              // framebuffer placement models a monitor, not the RGB pins.
              Video.prototype.blitFb.call(renderer, cell, 0, video.pixelsPerChar);
            }
            const start = video.halfClock && !video.oddClock ? 8 : 0;
            for (let dot = start; dot < start + 8; dot++) {
              const c = dots[dot];
              pins.push((c & 255 ? 1 : 0) | ((c >>> 8) & 255 ? 2 : 0) | ((c >>> 16) & 255 ? 4 : 0));
            }
          }
          if (elapsed === event)
            video.ula.write(
              variant !== undefined ? 0 : 1,
              variant === "clock"
                ? (fast ? 0 : 16) | (mode << 2)
                : variant === "mode"
                  ? (fast ? 16 : 0) | (((mode + 1) & 3) << 2)
                  : (byte ? 240 : 0) | 7,
            );
        } while (paints === 5);
        const rgb = Buffer.alloc(256 * 248 * 3);
        for (let y = 0; y < 248; y++)
          for (let x = 0; x < 256; x++) {
            const c = latest[(80 + 2 * y) * 1024 + 264 + 2 * x],
              o = (y * 256 + x) * 3;
            rgb[o] = c & 255;
            rgb[o + 1] = (c >>> 8) & 255;
            rgb[o + 2] = (c >>> 16) & 255;
          }
        fs.writeFileSync(out + "/" + id + ".rgb", rgb);
        if (variant === "clock") {
          fs.writeFileSync(out + "/" + id + ".pins", Buffer.from(pins));
          fs.writeFileSync(out + "/" + id + ".sync", Buffer.from(sync));
        }
        periods.push(id + " " + elapsed);
        if (variant !== "clock" && elapsed !== 39936)
          throw Error("Unexpected original field period");
      }
  }

if (periods.length !== 200) throw Error("Incomplete original video fixtures");
fs.writeFileSync(out + "/periods.tsv", periods.join("\n") + "\n");
console.log("Original video: 200 cases; 12,697,600 pixels; eight own warm-state caches");
