// Hash the same complete bitmap aperture as strict checkpoint RGB comparison.
import { createHash } from "node:crypto";
export function bitmapFieldHash(rgba) {
  if (rgba.length !== 1024 * 625 * 4) throw Error("Invalid original frame digest input");
  const rgb = Buffer.allocUnsafe(512 * 248 * 3);
  let at = 0;
  for (let y = 80; y < 576; y += 2)
    for (let x = 264; x < 776; x++) {
      const p = (y * 1024 + x) * 4;
      rgb[at++] = rgba[p];
      rgb[at++] = rgba[p + 1];
      rgb[at++] = rgba[p + 2];
    }
  return createHash("sha256").update(rgb).digest("hex");
}
