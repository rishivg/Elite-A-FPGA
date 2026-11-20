// Exercise unchanged public Original advance with independent reference fixtures.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, output] = process.argv.slice(2);
const { Teletext } = await import(pathToFileURL(root + "/src/teletext.js"));
const chip = new Teletext();
const glyphs = [chip.normalGlyphs, chip.graphicsGlyphs, chip.separatedGlyphs];
let observations = 0;
for (let code = 0; code < 128; code++)
  for (let held = 0; held < 128; held++)
    for (let gfx = 0; gfx < 2; gfx++)
      for (let hold = 0; hold < 2; hold++)
        for (let dbl = 0; dbl < 2; dbl++)
          for (let sep = 0; sep < 2; sep++)
            for (let flags = 0; flags < 4; flags++) {
              const col = (code ^ held) & 7;
              const bg = (held >>> 3) & 7;
              const heldSet = held % 3;
              const nextSet = gfx ? (sep ? 2 : 1) : 0;
              const flash = !!(flags & 1);
              const conceal = !!(flags & 2);
              Object.assign(chip, {
                col,
                bg,
                gfx: !!gfx,
                holdChar: !!hold,
                dbl: !!dbl,
                sep: !!sep,
                flash,
                conceal,
                heldChar: held,
                heldGlyphs: glyphs[heldSet],
                nextGlyphs: glyphs[nextSet],
                wasDbl: false,
              });
              chip.dataQueue[0] = code;
              let nc = col,
                nb = bg,
                ng = !!gfx,
                ns = !!sep,
                nd = !!dbl,
                nh = !!hold,
                nf = flash,
                nconceal = conceal;
              if ((code >= 1 && code <= 7) || (code >= 17 && code <= 23)) {
                ng = !!(code & 16);
                nc = code & 7;
                nconceal = false;
              }
              switch (code) {
                case 8:
                  nf = true;
                  break;
                case 9:
                  nf = false;
                  break;
                case 12:
                  nd = false;
                  break;
                case 13:
                  nd = true;
                  break;
                case 24:
                  nconceal = true;
                  break;
                case 25:
                  ns = false;
                  break;
                case 26:
                  ns = true;
                  break;
                case 28:
                  nb = 0;
                  break;
                case 29:
                  nb = col;
                  break;
                case 30:
                  nh = true;
                  break;
                case 31:
                  nh = false;
                  break;
              }
              const useHeld = !!gfx && (!!hold || nh) && nd === !!dbl;
              const character = code < 32 ? (useHeld ? held : 32) : code;
              const cellSet = code < 32 && useHeld ? heldSet : nextSet;
              const expectedHeld =
                code < 32 ? (useHeld ? held : 32) : gfx ? (code & 32 ? code : held) : 32;
              const expectedHeldSet = code >= 32 && gfx && code & 32 ? nextSet : heldSet;
              chip.advance();
              if (
                chip.col !== nc ||
                chip.bg !== nb ||
                chip.gfx !== ng ||
                chip.sep !== ns ||
                chip.dbl !== nd ||
                chip.holdChar !== nh ||
                chip.flash !== nf ||
                chip.conceal !== nconceal ||
                chip.heldChar !== expectedHeld ||
                chip.heldGlyphs !== glyphs[expectedHeldSet] ||
                chip.nextGlyphs !== glyphs[ng ? (ns ? 2 : 1) : 0] ||
                chip.curGlyphs !== glyphs[cellSet] ||
                chip.cellGlyphIndex !== (character - 32) * 20 ||
                chip.oldDbl !== !!dbl ||
                chip.wasDbl !== (code === 13) ||
                chip.cellPalette !== ((nb << 5) | (col << 2)) ||
                chip.cellFlash !== (flash && nf) ||
                chip.cellConceal !== (conceal || nconceal)
              )
                throw Error(
                  `Original control mismatch ${code}/${held}/${gfx}/${hold}/${dbl}/${sep}/${flags}`,
                );
              observations++;
            }
fs.writeFileSync(output, JSON.stringify({ passed: true, observations }) + "\n");
