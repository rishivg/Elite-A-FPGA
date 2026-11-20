// Replay accepted original bus inputs through an unchanged original Video.
// Its complete CRTC/fetch/cursor stream must agree with the original CPU run.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createGunzip } from "node:zlib";
import { BufferedCapture } from "./reference_capture.mjs";

const [root, pinsPath, busPath, outputPath] = process.argv.slice(2);
const { Video, VDISPENABLE, EVERYTHINGENABLED } = await import(
  pathToFileURL(path.join(root, "src/video.js"))
);
const ram = Buffer.alloc(32768, 255);
ram.fill(0, 0, 256);
const video = new Video(false, new Uint32Array(1024 * 625), () => {});
let memoryAddress,
  observing = false;
video.reset(
  {
    videoRead: (address) => {
      if (observing) memoryAddress = address;
      return ram[address];
    },
  },
  { setVBlankInt() {} },
);
let line = 0,
  cursor = 0,
  shown = false,
  cell = 0;
const endLine = video.endOfScanline,
  endFrame = video.endOfFrame;
video.endOfScanline = function (...args) {
  line = (line + 1) & 1023;
  return endLine.apply(this, args);
};
video.endOfFrame = function (...args) {
  line = 0;
  return endFrame.apply(this, args);
};
const read = video.readVideoMem;
video.readVideoMem = function () {
  const result = read.call(this);
  if (!observing) shown = (this.dispEnabled & EVERYTHINGENABLED) === EVERYTHINGENABLED;
  return result;
};
const handleCursor = video.handleCursor;
video.handleCursor = function (...args) {
  cursor = +!!(this.cursorOnThisFrame && this.ulactrl & this.cursorTable[this.cursorDrawIndex]);
  return handleCursor.apply(this, args);
};

// Read one bus operation ahead without retaining millions of JavaScript rows.
const busFd = fs.openSync(busPath, "r");
const busBuffer = Buffer.alloc(1 << 20);
let text = "",
  finished = false;
function nextBus() {
  let end;
  while ((end = text.indexOf("\n")) < 0 && !finished) {
    const bytes = fs.readSync(busFd, busBuffer);
    if (!bytes) finished = true;
    else text += busBuffer.toString("utf8", 0, bytes);
  }
  if (end < 0) {
    if (text) throw Error("Truncated accepted bus operation");
    return null;
  }
  const row = text.slice(0, end).split(" ");
  text = text.slice(end + 1);
  if (row.length !== 4) throw Error("Invalid bus operation");
  return { cycle: Number(row[0]), kind: row[1], address: Number(row[2]), data: Number(row[3]) };
}
let bus = nextBus(),
  cycle = 16,
  slots = 0,
  bitmapSlots = 0,
  transactions = 0;
const output = new BufferedCapture(outputPath, true);
const dots = new Uint32Array(16);
const renderer = {
  fb32: dots,
  table4bpp: video.table4bpp,
  pixelColours: () => video.pixelColours(),
};
let remainder = Buffer.alloc(0);
for await (const block of fs.createReadStream(pinsPath).pipe(createGunzip())) {
  const bytes = remainder.length ? Buffer.concat([remainder, block]) : block;
  let offset = 0;
  for (; offset + 20 <= bytes.length; offset += 20) {
    const packet = bytes.subarray(offset, offset + 20);
    if (packet.readUInt32LE(0) !== cycle || packet[19]) throw Error("Invalid original pin clock");
    while (bus && bus.cycle <= cycle) {
      if (bus.cycle !== cycle) throw Error("Missed original bus operation");
      if (bus.kind === "ram") ram[bus.address] = bus.data;
      else if (bus.kind === "scroll") video.setScreenHwScroll(bus.address);
      else if (bus.kind === "crtc") video.crtc.write(bus.address, bus.data);
      else if (bus.kind === "ula") video.ula.write(bus.address, bus.data);
      else throw Error("Unknown original bus operation");
      transactions++;
      bus = nextBus();
    }
    const flags =
      +!!(video.dispEnabled & VDISPENABLE) |
      (+video.inVSync << 1) |
      (+video.inHSync << 2) |
      (+!!(video.cursorOn && !video.cursorOff) << 3) |
      (+!!video.cursorOnThisFrame << 4) |
      (+!video.doEvenFrameLogic << 5) |
      (+video.oddClock << 6);
    observing = true;
    memoryAddress = undefined;
    let data;
    try {
      data = video.readVideoMem();
    } finally {
      observing = false;
    }
    if (
      video.horizCounter !== packet.readUInt16LE(4) ||
      video.addr !== packet.readUInt16LE(6) ||
      line !== packet.readUInt16LE(8) ||
      ((video.scanlineCounter & 30) | +!!video.teletext.levelRA0) !== packet[10] ||
      flags !== (packet[11] & 127) ||
      video.ulactrl !== packet[12] ||
      video.regs[1] !== packet[13] ||
      video.regs[8] !== packet[14] ||
      data !== packet[15] ||
      memoryAddress !== packet.readUInt16LE(16)
    )
      throw Error(`Original video replay differs before slot ${cycle}`);
    const odd = video.oddClock,
      fast = !!(video.ulactrl & 16),
      teletext = !!(video.ulactrl & 2);
    if (fast || !odd) {
      shown = false;
      cursor = 0;
      cell = data;
    }
    video.polltime(1);
    if (
      (+video.inVSync | (+video.inHSync << 1)) !== packet[18] ||
      ((fast || !odd) && cursor !== packet[11] >> 7)
    )
      throw Error(`Original video replay differs after slot ${cycle}`);
    const pixels = Buffer.alloc(8);
    if (!teletext) {
      dots.fill(0xff000000);
      renderer.ulaMode = video.ulaMode;
      if (shown) Video.prototype.blitFb.call(renderer, cell, 0, fast ? 8 : 16);
      const start = !fast && odd ? 8 : 0;
      for (let i = 0; i < 8; i++) {
        const color = dots[start + i];
        pixels[i] =
          ((color & 255 ? 1 : 0) | ((color >>> 8) & 255 ? 2 : 0) | ((color >>> 16) & 255 ? 4 : 0)) ^
          (cursor ? 7 : 0);
      }
      bitmapSlots++;
    }
    output.append(pixels);
    slots++;
    cycle++;
  }
  remainder = Buffer.from(bytes.subarray(offset));
}
if (remainder.length) throw Error("Truncated original pin stream");
// Accepted writes at the final endpoint affect final RAM, not preceding dots.
while (bus && bus.cycle === cycle && bus.kind === "ram") {
  ram[bus.address] = bus.data;
  transactions++;
  bus = nextBus();
}
if (bus || nextBus()) throw Error("Unconsumed original bus inputs");
fs.closeSync(busFd);
output.close();
fs.writeFileSync(outputPath + ".ram", ram);
console.log(
  JSON.stringify({
    slots,
    bitmap_slots: bitmapSlots,
    bitmap_dots: bitmapSlots * 8,
    transactions,
    uncompressed_bytes: output.bytes,
    scope:
      "Unchanged original Video/ULA and own power-on RAM; every recorded CRTC, sync, fetch and cursor input cross-checked against the independent original CPU capture.",
  }),
);
