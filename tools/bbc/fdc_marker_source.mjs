import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerByteControl } from "./fdc_byte_source.mjs";
export function lowerMarker(source, disc) {
  lowerByteControl(source);
  const program = parsers.babel.parse(source).program.body;
  const classes = program.filter(
    (n) =>
      n.type === "ExportNamedDeclaration" &&
      n.declaration?.type === "ClassDeclaration" &&
      n.declaration.id.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Changed controller class");
  const methods = classes[0].declaration.body.body;
  const get = (name, params) => {
    const a = methods.filter((n) => n.key?.name === name);
    if (
      a.length !== 1 ||
      a[0].kind !== "method" ||
      a[0].static ||
      a[0].computed ||
      a[0].async ||
      a[0].generator ||
      a[0].params.length !== params.length ||
      a[0].params.some((n, i) => n.type !== "Identifier" || n.name !== params[i])
    )
      throw Error("Changed marker method " + name);
    return a[0].body.body;
  };
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
  const constants = new Map();
  for (const n of program) {
    if (n.type !== "VariableDeclaration") continue;
    for (const d of n.declarations) {
      if (!["Registers", "State", "Result", "StatusFlag"].includes(d.id?.name)) continue;
      if (
        d.init?.callee?.object?.name !== "Object" ||
        d.init.callee.property?.name !== "freeze" ||
        d.init.arguments.length !== 1 ||
        d.init.arguments[0].type !== "ObjectExpression"
      )
        throw Error("Changed constants");
      for (const p of d.init.arguments[0].properties) {
        if (
          p.type !== "ObjectProperty" ||
          p.computed ||
          p.value.type !== "NumericLiteral" ||
          !Number.isInteger(p.value.value) ||
          p.value.value < 0 ||
          p.value.value > 255
        )
          throw Error("Nonbyte constant");
        const k = d.id.name + "." + p.key.name;
        if (constants.has(k)) throw Error("Duplicate constant");
        constants.set(k, p.value.value);
      }
    }
  }
  for (const [k, v] of Object.entries({
    "Registers.internalCommand": 31,
    "Registers.internalResult": 22,
    "State.checkDataMarker": 8,
    "State.inData": 9,
  }))
    if (constants.get(k) !== v) throw Error("Changed marker alias " + k);
  const dc = parsers.babel
    .parse(disc)
    .program.body.filter(
      (n) =>
        n.type === "ExportNamedDeclaration" &&
        n.declaration?.type === "ClassDeclaration" &&
        n.declaration.id.name === "IbmDiscFormat",
    );
  if (dc.length !== 1) throw Error("Changed disc format class");
  for (const name of ["markClockPattern", "dataMarkDataPattern", "deletedDataMarkDataPattern"]) {
    const a = dc[0].declaration.body.body.filter((n) => n.key?.name === name);
    if (
      a.length !== 1 ||
      a[0].kind !== "get" ||
      !a[0].static ||
      a[0].computed ||
      a[0].params.length ||
      a[0].body.body.length !== 1 ||
      a[0].body.body[0].type !== "ReturnStatement" ||
      a[0].body.body[0].argument.type !== "NumericLiteral"
    )
      throw Error("Changed FM constant");
    const value = a[0].body.body[0].argument.value;
    if (!Number.isInteger(value) || value < 0 || value > 255) throw Error("Nonbyte FM constant");
    constants.set("IbmDiscFormat." + name, value);
  }
  const body = get("_byteCallbackReading", ["dataByte", "clockByte"]);
  if (
    body.length !== 3 ||
    body[0].type !== "VariableDeclaration" ||
    body[0].declarations.length !== 1 ||
    body[0].declarations[0].id.name !== "command" ||
    member(body[0].declarations[0].init) !== "_internalCommand" ||
    body[1].type !== "IfStatement" ||
    member(body[1].test) !== "_irqCallbacks" ||
    body[1].alternate ||
    body[2].type !== "SwitchStatement" ||
    member(body[2].discriminant) !== "_state"
  )
    throw Error("Changed quiet reading prelude");
  const cases = body[2].cases.filter(
    (n) =>
      n.test?.type === "MemberExpression" &&
      !n.test.computed &&
      n.test.object.name === "State" &&
      n.test.property.name === "checkDataMarker",
  );
  if (
    cases.length !== 1 ||
    cases[0].consequent.length !== 2 ||
    cases[0].consequent[1].type !== "BreakStatement"
  )
    throw Error("Changed marker case");
  const locals = new Set(),
    lines = [],
    calls = new Map();
  const called = (name) => calls.set(name, (calls.get(name) || 0) + 1);
  const byte = (code) => ({ kind: "byte", code }),
    bool = (code) => ({ kind: "bool", code });
  function expression(n) {
    if (n.type === "NumericLiteral" && Number.isInteger(n.value) && n.value >= 0 && n.value <= 255)
      return byte("8'd" + n.value);
    if (n.type === "BooleanLiteral") return bool(n.value ? "1'b1" : "1'b0");
    if (n.type === "Identifier") {
      if (n.name === "dataByte" || n.name === "clockByte") return byte(n.name);
      if (locals.has(n.name)) return bool(n.name);
    }
    if (n.type === "MemberExpression") {
      if (
        !n.computed &&
        n.object.type === "Identifier" &&
        constants.has(n.object.name + "." + n.property.name)
      )
        return byte("8'd" + constants.get(n.object.name + "." + n.property.name));
      if (
        n.computed &&
        member(n.object) === "_regs" &&
        n.property.type === "MemberExpression" &&
        !n.property.computed &&
        n.property.object.name === "Registers" &&
        n.property.property.name === "internalCommand"
      )
        return byte("commandByte");
    }
    if (n.type === "BinaryExpression" || n.type === "LogicalExpression") {
      const a = expression(n.left),
        b = expression(n.right),
        op = n.operator;
      if (["===", "!=="].includes(op) && a.kind === b.kind)
        return bool("(" + a.code + (op === "===" ? " == " : " != ") + b.code + ")");
      if (op === "&" && a.kind === "byte" && b.kind === "byte")
        return byte("(" + a.code + " & " + b.code + ")");
      if (["&&", "||"].includes(op) && a.kind === "bool" && b.kind === "bool")
        return bool("(" + a.code + " " + op + " " + b.code + ")");
    }
    throw Error("Unsupported marker expression " + n.type);
  }
  function statement(n) {
    if (n.type === "BlockStatement") {
      for (const b of n.body) statement(b);
      return;
    }
    if (n.type === "VariableDeclaration") {
      if (n.kind !== "let" || n.declarations.length !== 1) throw Error("Changed marker local");
      const d = n.declarations[0];
      if (d.id.type !== "Identifier" || d.id.name !== "doIrqs" || locals.has(d.id.name))
        throw Error("Unexpected marker local");
      const v = expression(d.init);
      if (v.kind !== "bool") throw Error("Nonboolean IRQ local");
      locals.add(d.id.name);
      lines.push(d.id.name + "=" + v.code + ";");
      return;
    }
    if (n.type === "IfStatement") {
      const test = expression(n.test);
      if (test.kind !== "bool") throw Error("Nonboolean marker test");
      lines.push("if(" + test.code + ")begin");
      statement(n.consequent);
      lines.push("end");
      if (n.alternate) {
        lines.push("else begin");
        statement(n.alternate);
        lines.push("end");
      }
      return;
    }
    if (n.type === "ExpressionStatement") {
      const e = n.expression;
      if (e.type === "AssignmentExpression" && e.operator === "=") {
        if (e.left.type === "Identifier" && locals.has(e.left.name)) {
          const v = expression(e.right);
          if (v.kind !== "bool") throw Error("Changed IRQ assignment");
          lines.push(e.left.name + "=" + v.code + ";");
          return;
        }
        if (member(e.left) === "_crc") {
          const call = e.right,
            init = call.arguments?.[0];
          if (
            call.type !== "CallExpression" ||
            call.callee.type !== "MemberExpression" ||
            call.callee.computed ||
            call.callee.object.name !== "IbmDiscFormat" ||
            call.callee.property.name !== "crcAddByte" ||
            call.arguments.length !== 2 ||
            init.type !== "CallExpression" ||
            init.callee.type !== "MemberExpression" ||
            init.callee.computed ||
            init.callee.object.name !== "IbmDiscFormat" ||
            init.callee.property.name !== "crcInit" ||
            init.arguments.length !== 1 ||
            init.arguments[0].type !== "BooleanLiteral" ||
            init.arguments[0].value !== false ||
            call.arguments[1].type !== "Identifier" ||
            call.arguments[1].name !== "dataByte"
          )
            throw Error("Changed marker CRC API");
          called("crc");
          lines.push("crcWritten=1'b1;");
          return;
        }
      }
      if (e.type === "CallExpression") {
        const method = member(e.callee),
          a = e.arguments;
        if (method === "_startIrqCallbacks" && a.length === 0) {
          called(method);
          lines.push("starting=1'b1;");
          return;
        }
        const targets = {
          _setResult: ["resultWritten", "resultValue"],
          _setState: ["stateWritten", "nextState"],
          _finishCommand: ["finished", "finishResult"],
        };
        if (Object.hasOwn(targets, method) && a.length === 1) {
          called(method);
          const v = expression(a[0]);
          if (v.kind !== "byte") throw Error("Nonbyte delegated argument");
          lines.push(targets[method][0] + "=1'b1;" + targets[method][1] + "=" + v.code + ";");
          return;
        }
      }
    }
    throw Error("Unsupported marker effect " + n.type);
  }
  statement(cases[0].consequent[0]);
  for (const name of ["crc", "_startIrqCallbacks", "_setResult", "_setState", "_finishCommand"])
    if (calls.get(name) !== 1) throw Error("Changed marker call inventory " + name);
  const start = get("_startIrqCallbacks", []),
    startExpr = start[0]?.expression;
  if (
    start.length !== 1 ||
    start[0].type !== "ExpressionStatement" ||
    startExpr.type !== "AssignmentExpression" ||
    startExpr.operator !== "|=" ||
    startExpr.left.type !== "MemberExpression" ||
    !startExpr.left.computed ||
    member(startExpr.left.object) !== "_regs" ||
    startExpr.left.property.type !== "MemberExpression" ||
    startExpr.left.property.computed ||
    startExpr.left.property.object.name !== "Registers" ||
    startExpr.left.property.property.name !== "internalStatus" ||
    startExpr.right.type !== "NumericLiteral" ||
    startExpr.right.value !== 48
  )
    throw Error("Changed IRQ-start projection");
  const wb = get("_writeRegister", ["reg", "val"]),
    mask = wb[0]?.expression,
    b = wb[1],
    assign = b?.consequent?.body?.[0]?.expression;
  if (
    wb[0]?.type !== "ExpressionStatement" ||
    mask?.type !== "AssignmentExpression" ||
    mask.operator !== "&=" ||
    mask.left.name !== "reg" ||
    mask.right.type !== "NumericLiteral" ||
    mask.right.value !== 63 ||
    b?.type !== "IfStatement" ||
    b.alternate ||
    b.test.type !== "BinaryExpression" ||
    b.test.operator !== "<" ||
    b.test.left.name !== "reg" ||
    b.test.right.type !== "MemberExpression" ||
    b.test.right.object.name !== "IntelFdc" ||
    b.test.right.property.name !== "NumRegisters" ||
    b.consequent.type !== "BlockStatement" ||
    b.consequent.body.length !== 2 ||
    assign?.type !== "AssignmentExpression" ||
    assign.operator !== "=" ||
    assign.left.type !== "MemberExpression" ||
    !assign.left.computed ||
    member(assign.left.object) !== "_regs" ||
    assign.left.property.name !== "reg" ||
    assign.right.name !== "val" ||
    b.consequent.body[1].type !== "ReturnStatement" ||
    b.consequent.body[1].argument
  )
    throw Error("Changed typed result-register write prefix");
  const expectedExternal = parsers.babel.parse(
    "class Frame{_updateExternalStatus(){let status=this.internalStatus&(StatusFlag.busy|StatusFlag.nmi|StatusFlag.needData);if(this._isResultReady)status|=StatusFlag.resultReady;this._status=status;}}",
  ).program.body[0].body.body[0].body.body;
  function semantic(value) {
    if (Array.isArray(value)) return value.map(semantic);
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .filter(
            (k) =>
              ![
                "loc",
                "start",
                "end",
                "extra",
                "leadingComments",
                "trailingComments",
                "innerComments",
              ].includes(k),
          )
          .map((k) => [k, semantic(value[k])]),
      );
    return value;
  }
  if (
    JSON.stringify(semantic(get("_updateExternalStatus", []))) !==
    JSON.stringify(semantic(expectedExternal))
  )
    throw Error("Changed external-status frame");
  for (const [name, value] of Object.entries({ busy: 128, nmi: 8, needData: 4, resultReady: 16 }))
    if (constants.get("StatusFlag." + name) !== value) throw Error("Changed external-status alias");
  const setter = get("_setResult", ["result"]);
  const setValue = setter[0]?.expression,
    setReady = setter[1]?.expression,
    external = setter[2]?.expression;
  if (
    setter.length !== 3 ||
    setValue?.type !== "AssignmentExpression" ||
    setValue.operator !== "=" ||
    setValue.left.type !== "MemberExpression" ||
    !setValue.left.computed ||
    member(setValue.left.object) !== "_regs" ||
    setValue.left.property.type !== "MemberExpression" ||
    setValue.left.property.computed ||
    setValue.left.property.object.name !== "Registers" ||
    setValue.left.property.property.name !== "internalResult" ||
    setValue.right.type !== "Identifier" ||
    setValue.right.name !== "result" ||
    setReady?.type !== "AssignmentExpression" ||
    setReady.operator !== "=" ||
    member(setReady.left) !== "_isResultReady" ||
    setReady.right.type !== "BooleanLiteral" ||
    setReady.right.value !== true ||
    external?.type !== "CallExpression" ||
    member(external.callee) !== "_updateExternalStatus" ||
    external.arguments.length !== 0
  )
    throw Error("Changed internal result-set projection");
  return `module FdcMarkerSource(input[7:0]commandByte,dataByte,clockByte,output reg starting,resultWritten,stateWritten,finished,crcWritten,output reg[7:0]resultValue,nextState,finishResult);\nreg ${[...locals].join(",")};\nalways @(*)begin\nstarting=0;resultWritten=0;stateWritten=0;finished=0;crcWritten=0;resultValue=0;nextState=0;finishResult=0;${[...locals].map((n) => n + "=0;").join("")}\n${lines.join("\n")}\nend\nendmodule\nmodule FdcResultWriteSource(input[7:0]value,output[7:0]followingResult);assign followingResult=value;endmodule\nmodule FdcResultSetSource(input[7:0]oldResult,value,input oldReady,output[7:0]followingResult,output followingReady);assign followingResult=value;assign followingReady=1'b1;endmodule\nmodule FdcStartIrqSource(input[7:0]status,output[7:0]followingStatus);assign followingStatus=status|8'd${startExpr.right.value};endmodule\nmodule FdcExternalStatusSource(input[7:0]status,input ready,output[7:0]value);assign value=(status & 8'd140)|(ready?8'd16:8'd0);endmodule\n`;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(
    directory + "/source.v",
    lowerMarker(
      fs.readFileSync(root + "/src/intel-fdc.js", "utf8"),
      fs.readFileSync(root + "/src/disc.js", "utf8"),
    ),
  );
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const f = Object.create(IntelFdc.prototype);
  f._regs = new Uint8Array(32);
  let flags = 0,
    result = 0,
    state = 0,
    finish = 0,
    crc = 0;
  f._startIrqCallbacks = () => {
    flags |= 1;
  };
  f._setResult = (v) => {
    flags |= 2;
    result = v;
  };
  f._setState = (v) => {
    flags |= 4;
    state = v;
  };
  f._finishCommand = (v) => {
    flags |= 8;
    finish = v;
  };
  Object.defineProperty(f, "_crc", {
    get: () => crc,
    set: (v) => {
      flags |= 16;
      crc = v;
    },
  });
  const bytes = Buffer.alloc(256 * 65536 * 9);
  let offset = 0;
  for (let command = 0; command < 256; command++)
    for (let word = 0; word < 65536; word++) {
      f._regs[31] = command;
      f._regs[23] = 0;
      f._state = 8;
      flags = 0;
      result = 0;
      state = 0;
      finish = 0;
      crc = 0;
      f._byteCallbackReading(word & 255, word >>> 8);
      bytes[offset++] = command;
      bytes[offset++] = word >>> 8;
      bytes[offset++] = word & 255;
      bytes[offset++] = flags;
      bytes[offset++] = result;
      bytes[offset++] = state;
      bytes[offset++] = finish;
      bytes.writeUInt16LE(crc, offset);
      offset += 2;
    }
  fs.writeFileSync(directory + "/original.bin", bytes);
  const values = Buffer.alloc(256);
  for (let value = 0; value < 256; value++) {
    f._writeRegister(22, value);
    values[value] = f._regs[22];
  }
  fs.writeFileSync(directory + "/result.bin", values);
  const starts = Buffer.alloc(256);
  for (let status = 0; status < 256; status++) {
    f._regs[23] = status;
    IntelFdc.prototype._startIrqCallbacks.call(f);
    starts[status] = f._regs[23];
  }
  fs.writeFileSync(directory + "/irq.bin", starts);
  const external = Buffer.alloc(512);
  for (let status = 0; status < 256; status++)
    for (const ready of [false, true]) {
      f._regs[23] = status;
      f._isResultReady = ready;
      IntelFdc.prototype._updateExternalStatus.call(f);
      external[2 * status + +ready] = f._status;
    }
  fs.writeFileSync(directory + "/external.bin", external);
  const setters = Buffer.alloc(256 * 256 * 2 * 5);
  let index = 0;
  for (let old = 0; old < 256; old++)
    for (let value = 0; value < 256; value++)
      for (const ready of [false, true]) {
        f._regs[22] = old;
        f._isResultReady = ready;
        IntelFdc.prototype._setResult.call(f, value);
        setters.set([old, value, +ready, f._regs[22], +f._isResultReady], index);
        index += 5;
      }
  fs.writeFileSync(directory + "/setter.bin", setters);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({
      passed: true,
      marker_cases: 16777216,
      result_cases: 256,
      setter_cases: 131072,
      irq_cases: 256,
      external_cases: 512,
      registers: 32,
    }) + "\n",
  );
  console.log("16777216 original marker decisions; 256 result-register writes");
}
