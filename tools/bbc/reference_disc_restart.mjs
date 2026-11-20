// Original public FDC resets during independently executed host commands.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { configureDrive } from "./reference_drive.mjs";
const [root, ssd, output] = process.argv.slice(2);
if (!root || !ssd || !output || process.argv.length !== 5)
  throw Error("Expected pinned jsbeeb, SSD and output");
const { IntelFdc } = await import(pathToFileURL(path.join(root, "src/intel-fdc.js")));
const { Scheduler } = await import(pathToFileURL(path.join(root, "src/scheduler.js")));
const { discFor } = await import(pathToFileURL(path.join(root, "src/fdc.js")));
let scheduler, fdc, bus, nmi;
const cpu = { setNmi: (_source, level) => (nmi = level), nmiAsserted: () => nmi };
function fresh() {
  scheduler = new Scheduler();
  nmi = false;
  bus = [];
  fdc = new IntelFdc(cpu, scheduler);
  fdc.loadDisc(0, discFor("elite.ssd", new Uint8Array(fs.readFileSync(ssd))));
  configureDrive(fdc, "clocked-fm");
  command(0x35, 0x0d, 12, 10, 0xc8);
  command(0x35, 0x10, 255, 255, 0);
  command(0x35, 0x18, 255, 255, 0);
  command(0x3a, 0x17, 0xc1);
  command(0x3a, 0x11, 0xc1);
}
function write(address, value) {
  bus.push([scheduler.epoch, "W", address, value, +nmi]);
  fdc.write(address, value);
  scheduler.polltime(10);
}
function read(address) {
  const level = +nmi,
    value = fdc.read(address);
  bus.push([scheduler.epoch, "R", address, value, level]);
  scheduler.polltime(2);
  return value;
}
function command(opcode, ...parameters) {
  write(0, opcode);
  for (const value of parameters) write(1, value);
}
function request() {
  const deadline = scheduler.epoch + 2000000;
  while (!nmi && scheduler.epoch < deadline) scheduler.polltime(1);
  if (!nmi) throw Error("Original request did not arrive");
  if (!(read(0) & 4)) throw Error("Expected original byte request");
}
function prime() {
  command(0x3a, 0x17, 0xc1);
  command(0x3a, 0x23, 0x48);
  scheduler.polltime(850000);
  command(0x6c);
  read(1);
  command(0x6c);
  if (!(read(1) & 4)) throw Error("Original drive did not become ready");
}
function restart(name) {
  const epoch = scheduler.epoch;
  fdc.reset();
  bus.push([epoch, "B", 0, fdc.read(0), +nmi]);
  scheduler.polltime(2);
  if (nmi || (read(0) & 0x8c) !== 0) throw Error("Original restart did not abort the controller");
  // Observe retained data/result and programmable values through public reads.
  const result = read(1),
    data = read(4);
  command(0x3d, 0x11);
  if (read(1) !== 0xc1) throw Error("Original restart lost a stored special register");
  scheduler.polltime(2048);
  if (nmi || read(0) & 0x8c) throw Error("Original old command resumed after restart");
  return { name, epoch, result, data };
}
const cases = [];
for (const name of [
  "idle",
  "parameters",
  "partial-specify",
  "seek",
  "search-or-settle",
  "read-request",
  "write-request",
  "one-queued-write",
  "two-queued-writes",
  "late-service-error",
  "data-aliases",
  "host-reset-retention",
  "read-request-aliases",
  "write-request-aliases",
]) {
  fresh();
  if (
    !["idle", "parameters", "partial-specify", "data-aliases", "host-reset-retention"].includes(
      name,
    )
  )
    prime();
  switch (name) {
    case "idle":
      break;
    case "parameters":
      write(0, 0x53);
      break;
    case "partial-specify":
      command(0x35, 0x10, 0x91);
      break;
    case "seek":
      command(0x53, 2, 0, 0x21);
      scheduler.polltime(20);
      break;
    case "search-or-settle":
      command(0x53, 2, 0, 0x21);
      scheduler.polltime(10000);
      break;
    case "read-request":
      command(0x53, 0, 0, 0x21);
      request();
      break;
    case "write-request":
      command(0x4b, 0, 1, 0x21);
      request();
      break;
    case "one-queued-write":
      command(0x4b, 0, 1, 0x21);
      request();
      write(4, 0x71);
      break;
    case "two-queued-writes":
      command(0x4b, 0, 1, 0x21);
      request();
      write(4, 0x72);
      request();
      write(4, 0x73);
      break;
    case "late-service-error":
      command(0x53, 0, 0, 0x21);
      request();
      scheduler.polltime(512);
      break;
    case "data-aliases":
      for (let address = 4; address < 8; address++) {
        write(address, 0x70 + address);
        for (let alias = 4; alias < 8; alias++)
          if (read(alias) !== 0x70 + address) throw Error("Original data aliases differ");
      }
      break;
    case "host-reset-retention": {
      write(6, 0xa6);
      command(0x3d, 0x11);
      const before = read(0);
      write(2, 1);
      if (read(0) !== (before & 0x10)) throw Error("Register reset lost result-ready");
      if (read(1) !== 0xc1 || read(7) !== 0xa6) throw Error("Register reset lost result/data");
      break;
    }
    case "read-request-aliases":
      command(0x53, 0, 0, 0x21);
      for (const address of [7, 6, 5, 4]) {
        request();
        read(address);
        if (nmi || read(0) & 4) throw Error("Data alias did not acknowledge read request");
      }
      break;
    case "write-request-aliases":
      command(0x4b, 0, 1, 0x21);
      for (const address of [7, 6, 5, 4]) {
        request();
        write(address, 0x70 + address);
        if (nmi || read(0) & 4) throw Error("Data alias did not acknowledge write request");
      }
      break;
    default:
      throw Error("Unknown restart case");
  }
  const observed = restart(name);
  if (name === "partial-specify") {
    command(0x3d, 0x10);
    if (read(1) !== 0x91) throw Error("Restart lost completed SPECIFY writes");
  }
  const fixture = path.basename(output) + "." + name + ".bus.tsv";
  fs.writeFileSync(
    path.join(path.dirname(output), fixture),
    bus.map((row) => row.join(" ")).join("\n") + "\n",
  );
  cases.push({ ...observed, rows: bus.length, fixture });
}
fs.writeFileSync(
  output,
  JSON.stringify({
    cases,
    rows: cases.reduce((n, c) => n + c.rows, 0),
    profile: "clocked-fm",
    own_cold_cases: true,
  }) + "\n",
);
