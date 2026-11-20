// Check the unchanged Original cursor methods against the formal transition rules.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const video = new Video(false, new Uint32Array(1024 * 625), () => {});
let observations = 0;
for (let index = 1; index <= 6; index++)
  for (let control = 0; control < 256; control++)
    for (let visible = 0; visible < 2; visible++)
      for (let rgb = 0; rgb < 8; rgb++) {
        const fb = new Uint32Array(16);
        const pixel =
          0xff000000 | (rgb & 1 ? 255 : 0) | (rgb & 2 ? 65280 : 0) | (rgb & 4 ? 16711680 : 0);
        fb.fill(pixel);
        const observer = {
          fb32: fb,
          cursorTable: video.cursorTable,
          cursorDrawIndex: index,
          cursorOnThisFrame: !!visible,
          ulactrl: control,
          pixelsPerChar: 16,
          doubledScanlines: false,
          invertForCursor: Video.prototype.invertForCursor,
        };
        Video.prototype.handleCursor.call(observer, 0);
        const mask = index === 3 ? 128 : index === 4 ? 64 : index >= 5 ? 32 : 0;
        const invert = visible && control & mask;
        const expected = invert ? (pixel ^ 0x00ffffff) >>> 0 : pixel >>> 0;
        if (fb.some((value) => value !== expected) || observer.cursorDrawIndex !== (index + 1) % 7)
          throw Error(`Cursor rule mismatch ${index}/${control}/${visible}/${rgb}`);
        observations++;
      }
fs.writeFileSync(output, JSON.stringify({ passed: true, observations }) + "\n");
