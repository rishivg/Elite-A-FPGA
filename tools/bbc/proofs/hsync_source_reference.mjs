// Exhaust the finite horizontal-sync state against the unchanged JS method.
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, output] = process.argv.slice(2);
const { Video } = await import(pathToFileURL(root + "/src/video.js"));
const initial = new Video(false, new Uint32Array(1024 * 625), () => {});
if (initial.inHSync !== false || initial.hpulseCounter !== 0 || initial.hpulseWidth !== 0)
  throw Error("Changed original horizontal-sync constructor");
let cases = 0;
for (let active = 0; active < 2; active++)
  for (let count = 0; count < 16; count++)
    for (let width = 0; width < 16; width++)
      for (let hit = 0; hit < 2; hit++) {
        const original = {
          inHSync: !!active,
          hpulseCounter: count,
          hpulseWidth: width,
          regs: [0, 0, 0, width],
          bitmapX: 0,
          bitmapY: 0,
          hsyncCount: 0,
          subcarrierPhase: 0,
        };
        if (original.inHSync) Video.prototype.handleHSync.call(original);
        // This is the following initiation statement in original polltime.
        if (hit && !original.inHSync) {
          original.inHSync = true;
          original.hpulseCounter = 0;
        }
        let following = active ? (count + 1) & 15 : count;
        let sync = !!active;
        if (sync && following !== width >>> 1 && following === width) sync = false;
        if (hit && !sync) {
          sync = true;
          following = 0;
        }
        if (original.inHSync !== sync || original.hpulseCounter !== following)
          throw Error(`Horizontal-sync lowering failed: ${active},${count},${width},${hit}`);
        cases++;
      }
fs.writeFileSync(output, JSON.stringify({ cases, passed: true }) + "\n");
