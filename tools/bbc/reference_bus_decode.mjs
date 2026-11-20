// Check the unchanged Model B dispatch and wait-state APIs. Child callbacks
// identify routing only; their register behavior is checked separately.
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [root, output] = process.argv.slice(2);
const { Cpu6502 } = await import(pathToFileURL(root + "/src/6502.js"));
const cpu = Object.create(Cpu6502.prototype);
cpu.model = { isMaster: false };
cpu.econet = null;
cpu.tube = { read: () => 0xfe, write() {} };
const ids = { crtc: 0, acia: 1, serial: 2, sysvia: 3, uservia: 4, fdc: 5, adconverter: 6, ula: 7 };
let reads = 0,
  writes = 0;
for (const [name, id] of Object.entries(ids)) {
  cpu[name] = {
    read() {
      reads |= 1 << id;
      return 0xa0 + id;
    },
    write() {
      writes |= 1 << id;
    },
  };
}
cpu.romSelect = () => {
  writes |= 1 << 8;
};
const slow = cpu.buildIs1MHzAccess();
const rows = [];
for (let address = 0; address < 65536; address++) {
  reads = writes = 0;
  let defaultValue = 0xff;
  if (address >= 0xfc00 && address < 0xff00) {
    defaultValue = cpu.readDevice(address);
    cpu.writeDevice(address, 0x5a);
  }
  // Both absent Tube and other unimplemented SHEILA reads use FE. A marked
  // child read supplies its own value, so the default is immaterial there.
  rows.push([address, reads, writes, +slow(address, false), reads ? -1 : defaultValue].join(" "));
}
fs.writeFileSync(output, rows.join("\n") + "\n");
console.log("Original Model B decode: 65536 addresses");
