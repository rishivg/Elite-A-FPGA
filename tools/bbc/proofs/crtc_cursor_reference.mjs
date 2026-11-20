// Independent reference-only fixtures. No DUT state is read or assigned.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const video = new Video(false, new Uint32Array(1024 * 625), () => {});
let observations = 0;
for (let raster = 0; raster < 32; raster++)
  for (let start = 0; start < 32; start++)
    for (let end = 0; end < 32; end++)
      for (let interlace = 0; interlace < 2; interlace++)
        for (let flags = 0; flags < 4; flags++)
          for (let path = 0; path < 4; path++) {
            const count = (raster + start + end) & 31;
            const mode = (raster + end) & 3;
            const r9 = start & 1 ? raster : raster ^ 1;
            const adjusting = path === 1;
            const adjustEnd = path === 3;
            const dummy = path === 2;
            const fresh = dummy || (adjustEnd && !(interlace && count & 1));
            video.regs[8] = interlace ? 3 : 0;
            video.regs[9] = r9;
            video.regs[10] = start | (mode << 5);
            video.regs[11] = end;
            video.regs[12] = 0x12;
            video.regs[13] = 0x34;
            Object.assign(video, {
              vertCounter: 17,
              scanlineCounter: raster,
              vpulseCounter: 7,
              lineStartAddr: 0x2abc,
              nextLineStartAddr: 0x1def,
              inVertAdjust: adjusting,
              endOfMainLatched: false,
              endOfVertAdjustLatched: adjustEnd,
              endOfFrameLatched: dummy,
              inDummyRaster: dummy,
              frameCount: count,
              firstScanline: true,
              hadVSyncThisRow: true,
              cursorOn: !!(flags & 1),
              cursorOff: !!(flags & 2),
              cursorOnThisFrame: false,
              interlacedSyncAndVideo: !!interlace,
              teletextMode: false,
              dispEnabled: 0,
            });
            let expectedRaster = interlace ? (raster + 2) & 30 : (raster + 1) & 31;
            let expectedRow = 17;
            let expectedOn = !!(flags & 1);
            let expectedOff = !!(flags & 2) || raster === end;
            let expectedAddress = raster === r9 ? 0x1def : 0x2abc;
            if (!adjusting && raster === r9) {
              expectedRow = 18;
              expectedRaster = 0;
              expectedOn = false;
              expectedOff = false;
            }
            if (fresh) {
              expectedRow = 0;
              expectedRaster = 0;
              expectedAddress = 0x1234;
              expectedOn = false;
              expectedOff = false;
            }
            if (expectedRaster === start) expectedOn = true;
            video.endOfScanline();
            const visible =
              fresh && (mode === 0 || (mode === 2 && count & 8) || (mode === 3 && count & 16));
            if (
              video.scanlineCounter !== expectedRaster ||
              video.vertCounter !== expectedRow ||
              video.addr !== expectedAddress ||
              video.cursorOn !== expectedOn ||
              video.cursorOff !== expectedOff ||
              video.cursorOnThisFrame !== !!visible ||
              video.firstScanline !== fresh ||
              video.vpulseCounter !== 8
            )
              throw Error(
                `Original scanline mismatch ${raster}/${start}/${end}/${interlace}/${flags}/${path}`,
              );
            observations++;
          }
fs.writeFileSync(output, JSON.stringify({ passed: true, observations }) + "\n");
