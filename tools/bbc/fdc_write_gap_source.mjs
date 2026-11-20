import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerGap } from "./fdc_gap_source.mjs";
import { lowerByteControl } from "./fdc_byte_source.mjs";
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
const expected = (s) => parse("class Expected{" + s + "}")[0].body.body[0];
export function lowerWriteGap(source) {
  lowerGap(source);
  const program = parse(source),
    constants = new Map();
  for (const n of program)
    if (n.type === "VariableDeclaration")
      for (const d of n.declarations) {
        if (!["Registers", "DriveOut", "Call", "State"].includes(d.id?.name)) continue;
        if (
          d.init?.type !== "CallExpression" ||
          d.init.callee?.object?.name !== "Object" ||
          d.init.callee.property?.name !== "freeze" ||
          d.init.arguments.length !== 1 ||
          d.init.arguments[0].type !== "ObjectExpression"
        )
          throw Error("Changed write-run constants");
        for (const p of d.init.arguments[0].properties) {
          if (
            p.type !== "ObjectProperty" ||
            p.computed ||
            p.value.type !== "NumericLiteral" ||
            !Number.isInteger(p.value.value)
          )
            throw Error("Changed write-run constant");
          const k = d.id.name + "." + p.key.name;
          if (constants.has(k)) throw Error("Duplicate write-run constant");
          constants.set(k, p.value.value);
        }
      }
  for (const [k, v] of Object.entries({
    "Registers.internalWriteRunData": 21,
    "DriveOut.writeEnable": 1,
    "Call.write": 5,
    "State.writeRun": 12,
    "State.writeDataMark": 13,
  }))
    if (constants.get(k) !== v) throw Error("Changed write-run alias " + k);
  const classes = program.filter(
    (n) =>
      n.type === "ExportNamedDeclaration" &&
      n.declaration?.type === "ClassDeclaration" &&
      n.declaration.id.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Changed write-run class");
  const methods = classes[0].declaration.body.body;
  const get = (name) => {
    const a = methods.filter((n) => n.key?.name === name);
    if (a.length !== 1) throw Error("Changed write-run method " + name);
    return a[0];
  };
  const same = (a, b) => JSON.stringify(clean(a)) === JSON.stringify(clean(b));
  const init = expected(
    `_doWriteRun(callContext,byte){this._mmioData=byte&0xff;this._mmioClocks=0xff;this._crc=IbmDiscFormat.crcAddByte(this._crc,this._mmioData);this._regs[Registers.internalWriteRunData]=this._mmioData;this._driveOutRaise(DriveOut.writeEnable);this._callContext=callContext;this._setState(State.writeRun);}`,
  );
  if (!same(get("_doWriteRun"), init)) throw Error("Changed write-run initializer");
  const start = expected("_startIrqCallbacks(){this._regs[Registers.internalStatus]|=0x30;}");
  if (!same(get("_startIrqCallbacks"), start)) throw Error("Changed IRQ-start effect");
  const run = get("_callbackWriteRun");
  if (
    run.kind !== "method" ||
    run.static ||
    run.computed ||
    run.async ||
    run.generator ||
    run.params.length ||
    run.body.body.length !== 2
  )
    throw Error("Changed write-run callback");
  const prefix = expected(
    "_callbackWriteRun(){if(!this._decrementCounter()){this._mmioData=this._regs[Registers.internalWriteRunData];this._crc=IbmDiscFormat.crcAddByte(this._crc,this._mmioData);return;}}",
  ).body.body[0];
  if (!same(run.body.body[0], prefix)) throw Error("Changed write-run counter prelude");
  const sw = run.body.body[1],
    member = sw.discriminant;
  if (
    sw.type !== "SwitchStatement" ||
    member?.type !== "MemberExpression" ||
    member.computed ||
    member.object.type !== "ThisExpression" ||
    member.property.name !== "_callContext"
  )
    throw Error("Changed write-run dispatch");
  const caseValue = (n) =>
    n?.type === "NumericLiteral"
      ? n.value
      : n?.type === "MemberExpression" && !n.computed && n.object.type === "Identifier"
        ? constants.get(n.object.name + "." + n.property.name)
        : undefined;
  if (sw.cases.some((n) => n.test !== null && caseValue(n.test) === undefined))
    throw Error("Effectful or unknown write-run case label");
  const cases = sw.cases.filter((n) => caseValue(n.test) === 5);
  const want = expected(
    "_callbackWriteRun(){switch(this._callContext){case Call.write:this._mmioData=0;this._startIrqCallbacks();this._setState(State.writeDataMark);break;}}",
  ).body.body[0].cases[0];
  if (cases.length !== 1 || !same(cases[0], want))
    throw Error("Changed write-run write continuation");
  return (
    lowerByteControl(source) +
    `\nmodule FdcWriteRunSource(input[7:0]low,high,status,runData,output[7:0]nextLow,nextHigh,nextStatus,nextData,nextState,output stateWritten,crcCalled);
 wire[7:0]counterStatus,result;wire done,aborted,finished;
 FdcCounterSource counter(.low(low),.high(high),.status(status),.command(8'd8),.nextLow(nextLow),.nextHigh(nextHigh),.nextStatus(counterStatus),.value(done),.aborted(aborted),.finished(finished),.finishResult(result));
 assign nextStatus=done?(counterStatus|8'h30):counterStatus;
 assign nextData=done?8'd0:runData;
 assign nextState=done?8'd13:8'd0;
 assign stateWritten=done;assign crcCalled=!done;
 endmodule
 module FdcWriteRunInitSource(input[7:0]value,contextByte,output[7:0]runData,dataByte,clocksByte,nextContext,nextState,output gateRaised,crcCalled);
 assign runData=value;assign dataByte=value;assign clocksByte=8'hff;assign nextContext=contextByte;assign nextState=8'd12;assign gateRaised=1'b1;assign crcCalled=1'b1;endmodule\n`
  );
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, dir] = process.argv.slice(2);
  fs.mkdirSync(dir, { recursive: true });
  const text = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  fs.writeFileSync(dir + "/source.v", lowerWriteGap(text));
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const { IbmDiscFormat } = await import(pathToFileURL(root + "/src/disc.js"));
  const fdc = Object.create(IntelFdc.prototype);
  fdc._regs = new Uint8Array(32);
  let state = 0,
    raised = 0,
    calls = 0;
  fdc._setState = (s) => {
    state = s;
  };
  fdc._driveOutRaise = (m) => {
    raised = m;
  };
  const crc = IbmDiscFormat.crcAddByte;
  IbmDiscFormat.crcAddByte = (...args) => {
    calls++;
    return crc(...args);
  };
  const init = Buffer.alloc(256 * 7);
  let p = 0;
  for (let v = 0; v < 256; v++) {
    state = raised = calls = 0;
    fdc._crc = 65535;
    fdc._doWriteRun(5, v);
    for (const x of [
      fdc._regs[21],
      fdc._mmioData,
      fdc._mmioClocks,
      fdc._callContext,
      state,
      raised,
      calls,
    ])
      init[p++] = x;
  }
  fs.writeFileSync(dir + "/init.bin", init);
  const rows = [];
  function run(low, high, status, data) {
    fdc._regs[19] = low;
    fdc._regs[20] = high;
    fdc._regs[21] = data;
    fdc._regs[23] = status;
    fdc._callContext = 5;
    fdc._crc = 65535;
    fdc._mmioData = 170;
    state = calls = 0;
    fdc._callbackWriteRun();
    rows.push(
      fdc._regs[19],
      fdc._regs[20],
      fdc._regs[23],
      fdc._mmioData,
      state ? 1 : 0,
      state,
      calls,
    );
  }
  for (let l = 0; l < 256; l++) for (let h = 0; h < 256; h++) run(l, h, 0, 85);
  for (let v = 0; v < 256; v++) run(2, 0, 0, v);
  for (let s = 0; s < 256; s++) for (const l of [1, 2]) run(l, 0, s, 85);
  fs.writeFileSync(dir + "/run.bin", Buffer.from(rows));
  fs.writeFileSync(
    dir + "/projection.json",
    JSON.stringify({
      passed: true,
      counter_cases: 65536,
      data_cases: 256,
      status_cases: 512,
      init_cases: 256,
    }) + "\n",
  );
}
