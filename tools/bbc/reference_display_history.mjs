// Execute the unchanged video polltime method over horizontal flag transitions.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video, HDISPENABLE, SKEWDISPENABLE } = await import(pathToFileURL(root + "/src/video.js"));
const video = new Video(false, new Uint32Array(1024 * 625), () => {});
video.reset({ videoRead: () => 0 }, null);
let observations = 0;
for (let horizontal = 0; horizontal < 256; horizontal++)
  for (let retained = 0; retained < 3; retained++)
    for (let teletext = 0; teletext < 2; teletext++)
      for (let hidden = 0; hidden < 2; hidden++)
        for (let flags = 0; flags < 4; flags++) {
          const position = retained + (teletext ? 2 : 0);
          const values = [0, horizontal, 255, (horizontal - position) & 255];
          for (const width of values)
            for (const total of values) {
              for (const [index, value] of [
                [0, total],
                [1, width],
                [2, 250],
                [3, 0],
                [4, 127],
                [5, 0],
                [6, 0],
                [7, 120],
                [9, 31],
              ]) {
                video.crtc.write(0, index);
                video.crtc.write(1, value);
              }
              video.crtc.write(0, 8);
              video.crtc.write(1, retained << 4);
              if (hidden) video.crtc.write(1, 3 << 4);
              video.ula.write(0, 16 | (teletext << 1));
              Object.assign(video, {
                horizCounter: horizontal,
                vertCounter: 0,
                scanlineCounter: 0,
                bitmapX: 0,
                bitmapY: 0,
                cursorDrawIndex: 0,
                oddClock: false,
                inHSync: false,
                inVSync: false,
                inVertAdjust: false,
                endOfMainLatched: false,
                endOfVertAdjustLatched: false,
                endOfFrameLatched: false,
                inDummyRaster: false,
                checkVertAdjust: false,
                firstScanline: false,
                dispEnabled: (flags & 1 ? HDISPENABLE : 0) | (flags & 2 ? SKEWDISPENABLE : 0),
              });
              let displayed = !!(flags & 1),
                skewed = !!(flags & 2);
              if (horizontal === position) skewed = true;
              if (horizontal === width + position || horizontal === total + position) {
                displayed = false;
                skewed = false;
              }
              if (horizontal === total) displayed = true;
              video.polltime(1);
              if (
                !!(video.dispEnabled & HDISPENABLE) !== displayed ||
                !!(video.dispEnabled & SKEWDISPENABLE) !== skewed ||
                video.displayEnableSkew !== retained
              )
                throw Error(
                  `Horizontal transition mismatch ${horizontal}/${width}/${total}/${retained}/${teletext}/${hidden}/${flags}`,
                );
              observations++;
            }
        }
if (observations !== 196608) throw Error("Incomplete display transition corpus");
fs.writeFileSync(output, JSON.stringify({ passed: true, observations }) + "\n");
