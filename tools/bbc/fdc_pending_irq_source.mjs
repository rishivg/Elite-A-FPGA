import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerWriteGap } from "./fdc_write_gap_source.mjs";
const clean = (n) =>
  Array.isArray(n)
    ? n.map(clean)
    : n && typeof n === "object"
      ? Object.fromEntries(
          Object.entries(n)
            .filter(
              ([k]) =>
                ![
                  "start",
                  "end",
                  "loc",
                  "extra",
                  "leadingComments",
                  "trailingComments",
                  "innerComments",
                  "comments",
                  "tokens",
                  "errors",
                ].includes(k),
            )
            .map(([k, v]) => [k, clean(v)]),
        )
      : n;
const parse = (s) => parsers.babel.parse(s).program.body;
const method = (s) => parse("class Expected{" + s + "}")[0].body.body[0];
export function lowerPendingPrefix(source) {
  const base = lowerWriteGap(source),
    program = parse(source),
    constants = new Map();
  for (const n of program)
    if (n.type === "VariableDeclaration")
      for (const d of n.declarations) {
        if (!["Registers", "StatusFlag", "State"].includes(d.id?.name)) continue;
        if (
          d.init?.type !== "CallExpression" ||
          d.init.callee?.object?.name !== "Object" ||
          d.init.callee.property?.name !== "freeze" ||
          d.init.arguments.length !== 1 ||
          d.init.arguments[0].type !== "ObjectExpression"
        )
          throw Error("Changed pending-prefix constants");
        for (const p of d.init.arguments[0].properties) {
          if (p.type !== "ObjectProperty" || p.computed || p.value.type !== "NumericLiteral")
            throw Error("Nonconstant prefix alias");
          const k = d.id.name + "." + p.key.name;
          if (constants.has(k)) throw Error("Duplicate prefix alias");
          constants.set(k, p.value.value);
        }
      }
  for (const [k, v] of Object.entries({
    "Registers.internalData": 29,
    "StatusFlag.nmi": 8,
    "StatusFlag.needData": 4,
  }))
    if (constants.get(k) !== v) throw Error("Changed prefix alias " + k);
  const cls = program.filter(
    (n) =>
      n.type === "ExportNamedDeclaration" &&
      n.declaration?.type === "ClassDeclaration" &&
      n.declaration.id.name === "IntelFdc",
  );
  if (cls.length !== 1) throw Error("Changed prefix class");
  const get = (name) => {
    const m = cls[0].declaration.body.body.filter((n) => n.key?.name === name);
    if (m.length !== 1) throw Error("Changed prefix method " + name);
    return m[0];
  };
  const same = (a, b) => JSON.stringify(clean(a)) === JSON.stringify(clean(b));
  const reading = get("_byteCallbackReading"),
    writing = get("_byteCallbackWriting");
  const wantedReading = method(
    "_byteCallbackReading(dataByte,clockByte){const command=this._internalCommand;if(this._irqCallbacks){if(!this._checkDataLossOk())return;this._regs[Registers.internalData]=dataByte;this._statusRaise(StatusFlag.nmi|StatusFlag.needData);}}",
  );
  if (
    !same(reading.params, wantedReading.params) ||
    !same(reading.body.body.slice(0, 2), wantedReading.body.body)
  )
    throw Error("Changed reading IRQ prefix");
  const wantedWriting = method(
    "_byteCallbackWriting(){if(this._irqCallbacks){if(!this._checkDataLossOk())return;this._statusRaise(StatusFlag.nmi|StatusFlag.needData);}}",
  );
  if (
    writing.kind !== "method" ||
    writing.static ||
    writing.computed ||
    writing.async ||
    writing.generator ||
    writing.params.length ||
    writing.body.body.length !== 2 ||
    writing.body.body[1].type !== "SwitchStatement" ||
    !same(writing.body.body[0], wantedWriting.body.body[0])
  )
    throw Error("Changed writing IRQ prefix");
  const sw = writing.body.body[1];
  if (
    sw.discriminant?.type !== "MemberExpression" ||
    sw.discriminant.computed ||
    sw.discriminant.object.type !== "ThisExpression" ||
    sw.discriminant.property.name !== "_state"
  )
    throw Error("Changed writing state dispatch");
  const label = (n) =>
    n?.type === "NumericLiteral"
      ? n.value
      : n?.type === "MemberExpression" && !n.computed && n.object.type === "Identifier"
        ? constants.get(n.object.name + "." + n.property.name)
        : undefined;
  if (sw.cases.some((n) => n.test !== null && label(n.test) === undefined))
    throw Error("Effectful writing state label");
  const selected = sw.cases.filter((n) => label(n.test) === 12);
  const expectedCase = method(
    "_byteCallbackWriting(){switch(this._state){case State.writeRun:this._callbackWriteRun();break;}}",
  ).body.body[0].cases[0];
  if (selected.length !== 1 || !same(selected[0], expectedCase))
    throw Error("Changed write-run body dispatch");
  return (
    base +
    `\nmodule FdcPendingPrefixSource(input[7:0]status,command,byteValue,oldData,input writingRun,output calledLoss,dataWritten,aborted,finished,bodyCalled,output[7:0]followingData,raiseMask,finishResult);
 wire irq,ok,rawAbort,rawFinish;wire[7:0]rawResult;
 FdcIrqSource irqSource(.low(8'd0),.high(8'd0),.status(status),.command(command),.value(irq));
 FdcLossSource lossSource(.low(8'd0),.high(8'd0),.status(status),.command(command),.value(ok),.aborted(rawAbort),.finished(rawFinish),.finishResult(rawResult));
 assign calledLoss=irq;assign bodyCalled=!irq||ok;
 assign dataWritten=irq&&ok&&!writingRun;
 assign followingData=dataWritten?byteValue:oldData;
 assign raiseMask=(irq&&ok)?8'd12:8'd0;
 assign aborted=irq&&rawAbort;assign finished=irq&&rawFinish;
 assign finishResult=finished?rawResult:8'd0;
 endmodule\n`
  );
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, dir] = process.argv.slice(2);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    dir + "/source.v",
    lowerPendingPrefix(fs.readFileSync(root + "/src/intel-fdc.js", "utf8")),
  );
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const fdc = Object.create(IntelFdc.prototype);
  let written = false,
    called = false,
    abort = false,
    finished = false,
    result = 0,
    raised = 0,
    lossCalls = 0;
  const bank = new Uint8Array(32);
  fdc._regs = new Proxy(bank, {
    set(target, key, value) {
      target[key] = value;
      if (key === "29") written = true;
      if (key === "21") called = true;
      return true;
    },
  });
  fdc._statusRaise = (mask) => {
    raised |= mask;
    bank[23] |= mask;
  };
  fdc._commandAbort = () => {
    abort = true;
  };
  fdc._finishCommand = (value) => {
    finished = true;
    result = value;
  };
  fdc._callbackWriteRun = () => {
    called = true;
  };
  fdc._checkDataLossOk = function () {
    lossCalls++;
    return IntelFdc.prototype._checkDataLossOk.call(this);
  };
  function run(status, command, value, writing, buffer, p) {
    bank[23] = status;
    bank[31] = command;
    bank[29] = 90;
    bank[21] = 2;
    fdc._state = writing ? 12 : 11;
    fdc._callContext = 4;
    written = called = abort = finished = false;
    result = raised = lossCalls = 0;
    if (writing) IntelFdc.prototype._byteCallbackWriting.call(fdc);
    else IntelFdc.prototype._byteCallbackReading.call(fdc, value, 255);
    for (const x of [
      lossCalls,
      written ? 1 : 0,
      bank[29],
      raised,
      abort ? 1 : 0,
      finished ? 1 : 0,
      result,
      called ? 1 : 0,
    ])
      buffer[p++] = x;
    return p;
  }
  const read = Buffer.alloc(256 * 256 * 256 * 8);
  let p = 0;
  for (let status = 0; status < 256; status++)
    for (let cmd = 0; cmd < 256; cmd++)
      for (let value = 0; value < 256; value++) p = run(status, cmd, value, false, read, p);
  fs.writeFileSync(dir + "/reading.bin", read);
  const write = Buffer.alloc(256 * 256 * 8);
  p = 0;
  for (let status = 0; status < 256; status++)
    for (let cmd = 0; cmd < 256; cmd++) p = run(status, cmd, 0, true, write, p);
  fs.writeFileSync(dir + "/writing.bin", write);
  fs.writeFileSync(
    dir + "/projection.json",
    JSON.stringify({
      passed: true,
      reading_cases: 16777216,
      writing_cases: 65536,
      old_data: 90,
      registers: 32,
    }) + "\n",
  );
}
