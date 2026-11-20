// Execute the Model B slow-address decode and both original bus deadline paths.
import fs from "node:fs";
import vm from "node:vm";

const [cpuPath, adapterPath] = process.argv.slice(2);
const cpu = fs.readFileSync(cpuPath, "utf8");
const adapter = fs.readFileSync(adapterPath, "utf8");

function one(text, pattern) {
  const matches = [...text.matchAll(pattern)];
  if (matches.length !== 1) throw Error(`Unexpected source boundary for ${pattern}`);
  return matches[0][0];
}

const addressDecode = one(cpu, /^function is1MHzAccess\(addr\) \{[\s\S]+?^\}/gm);
const polltimeAddr = one(cpu, /^    polltimeAddr\(cycles, addr, isWrite\) \{[\s\S]+?^    \}/gm);
const remaining = one(
  adapter,
  /remaining = cpu\.is1MHzAccess\(address, !\(pins & 0x01000000\)\)\s+\? [^;]+;/g,
);
const slow = vm.runInNewContext(`${addressDecode}\nis1MHzAccess`);
const original = vm.runInNewContext(`({${polltimeAddr}}).polltimeAddr`);
const adapted = vm.runInNewContext(
  `(cpu, address, pins) => { let remaining; ${remaining} return remaining; }`,
);

let result = Buffer.alloc(1 << 16);
let probe = {
  is1MHzAccess: slow,
  currentCycles: 0,
  polltime(slots) {
    this.slots = slots;
  },
};
for (let address = 0; address < 1 << 16; address++) {
  for (let parity = 0; parity < 2; parity++) {
    for (const isWrite of [false, true]) {
      probe.currentCycles = parity;
      original.call(probe, 1, address, isWrite);
      const actual = adapted(probe, address, isWrite ? 0 : 0x01000000);
      const expected = slow(address) ? 3 - parity : 1;
      if (actual !== probe.slots || actual !== expected)
        throw Error(`Unexpected deadline at ${address}`);
    }
    result[address] |= probe.slots << (2 * parity);
  }
}
process.stdout.write(result);
