// Exercise public Original ULA writes and its unmodified bitmap renderer.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, out] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const fb = new Uint32Array(1024 * 625);
const video = new Video(false, fb, () => {});
video.reset({ videoRead: () => 0 }, { setVBlankInt() {} });
let observations = 0;
for (let seed = 0; seed < 16; seed++) {
  for (let index = 0; index < 16; index++) video.ula.write(1, (index << 4) | ((index + seed) & 15));
  for (let flash = 0; flash < 2; flash++)
    for (let mode = 0; mode < 4; mode++) {
      video.ula.write(0, (mode << 2) | flash);
      for (let byte = 0; byte < 256; byte++) {
        video.blitFb(byte, 0, 16);
        for (let position = 0; position < 16; position++) {
          let shifted = byte;
          for (let i = 0; i < position >> (3 - mode); i++) shifted = ((shifted << 1) | 1) & 255;
          const index =
            ((shifted & 128) >> 4) |
            ((shifted & 32) >> 3) |
            ((shifted & 8) >> 2) |
            ((shifted & 2) >> 1);
          const colour = (index + seed) & 15;
          const expected = colour & 8 && flash ? colour & 7 : (colour & 7) ^ 7;
          const rgb =
            (fb[position] & 255 ? 1 : 0) |
            ((fb[position] >>> 8) & 255 ? 2 : 0) |
            ((fb[position] >>> 16) & 255 ? 4 : 0);
          if (rgb !== expected)
            throw Error(
              `Original palette/bitmap mismatch ${seed}/${flash}/${mode}/${byte}/${position}`,
            );
          observations++;
        }
      }
    }
}
fs.writeFileSync(
  out,
  JSON.stringify(
    {
      passed: true,
      observations,
      scope:
        "Public Original ULA palette/control writes and bitmap renderer; 16 palette mappings, both flash states, all four modes, 256 bytes and 16 positions.",
    },
    null,
    2,
  ) + "\n",
);
