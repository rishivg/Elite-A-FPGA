// Execute the pinned retry decision and seek arithmetic, without rewriting them.
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [root, directory] = process.argv.slice(2);
const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
const text = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
function constants(name) {
  const matches = [
    ...text.matchAll(
      new RegExp("const " + name + " = Object.freeze\\((\\{[\\s\\S]*?\\})\\);", "g"),
    ),
  ];
  assert.equal(matches.length, 1);
  return Function("return (" + matches[0][1] + ");")();
}
const Registers = constants("Registers"),
  Call = constants("Call"),
  DriveOut = constants("DriveOut");
const begin = "    _doSeek(callContext) {",
  end = "        this._regs[Registers.internalSeekTarget_1] = newTrack;";
assert.equal(text.split(begin).length, 2);
assert.equal(text.split(end).length, 2);
const prefix = text.slice(text.indexOf(begin) + begin.length, text.indexOf(end));
const body = prefix + "\nreturn newTrack;\n";
fs.writeFileSync(directory + "/seek-prefix.js", body);
const seek = Function("Registers", "Call", "DriveOut", "callContext", body);
const context = { _regs: new Uint8Array(32), _driveOut: 0x40, _callContext: 4 };
let cases = 0;
for (const drive of [0, 1]) {
  context._driveOut = drive ? 0x80 : 0x40;
  const bank = drive ? Registers.badTrack_1Drive_1 : Registers.badTrack_1Drive_0;
  for (let requested = 0; requested < 256; requested++)
    for (let retry = 0; retry < 3; retry++) {
      context._regs[Registers.internalParam_1] = requested;
      context._regs[Registers.internalSeekRetryCount] = retry;
      for (let a = 0; a < 256; a++) {
        context._regs[bank] = a;
        for (let b = 0; b < 256; b++) {
          context._regs[bank + 1] = b;
          const actual = seek.call(context, Registers, Call, DriveOut, Call.unchanged);
          const sum = requested + retry,
            skip0 = +(sum !== 0 && a <= sum);
          const skip1 = +(sum !== 0 && b <= ((sum + skip0) & 255));
          const expected = (sum + skip0 + skip1) & 255;
          if (actual !== expected)
            throw Error(
              `Seek target mismatch ${drive}/${requested}/${retry}/${a}/${b}: ${actual}/${expected}`,
            );
          cases++;
        }
      }
    }
}
const fdc = new IntelFdc({ setNmi() {}, nmiAsserted: () => false }, new Scheduler());
fdc._log = () => {};
let decision;
fdc._doSeek = (call) => {
  decision = ["retry", call, fdc._regs[1]];
};
fdc._finishCommand = (code) => {
  decision = ["finish", code, fdc._regs[1]];
};
const budget = [];
for (let count = 0; count < 256; count++) {
  fdc._regs.fill(0);
  fdc._regs[0x1f] = 0x10;
  fdc._regs[7] = 3;
  fdc._regs[0x0c] = 4;
  fdc._regs[1] = count;
  fdc._state = 6;
  fdc._stateCount = 1;
  fdc._crc = 0;
  fdc._onDiscCrc = 0;
  decision = null;
  fdc._byteCallbackReading(0, 0xff);
  assert.deepEqual(
    decision,
    count === 2 ? ["finish", 0x18, 3] : ["retry", Call.unchanged, (count + 1) & 255],
  );
  budget.push(decision);
}
const stepMs = [];
for (let rate = 0; rate < 256; rate++) {
  fdc._regs[Registers.headStepRate] = rate;
  stepMs.push(fdc._stepRateMs());
}
let headCases = 0,
  allocation;
fdc._currentDrive = null;
fdc._driveOutRaise = (bits) => {
  fdc._driveOut |= bits;
};
fdc._setTimerMs = (state, ms) => {
  allocation = [state, ms];
};
fdc._postSeekDispatch = () => {
  allocation = [0, 0];
};
for (const loaded of [false, true])
  for (const stepped of [false, true])
    for (let head = 0; head < 256; head++)
      for (let settle = 0; settle < 256; settle++) {
        fdc._driveOut = loaded ? 8 : 0;
        fdc._didSeekStep = stepped;
        fdc._regs[Registers.headLoadUnload] = head;
        fdc._regs[Registers.headSettleTime] = settle;
        allocation = null;
        fdc._doLoadHead();
        const ms = loaded ? (stepped ? settle * 2 : 0) : (head & 15) * 8;
        assert.deepEqual(allocation, ms ? [2, ms] : [0, 0]);
        headCases++;
      }
fs.writeFileSync(
  directory + "/retry-source.json",
  JSON.stringify({
    passed: true,
    target_cases: cases,
    budget,
    step_ms: stepMs,
    head_cases: headCases,
  }) + "\n",
);
