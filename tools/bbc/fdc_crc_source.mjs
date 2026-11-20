// Lower the original ID CRC body and default ID stop tail at explicit API boundaries.
import fs from "node:fs";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerByteControl } from "./fdc_byte_source.mjs";
export function lowerIdCrc(source) {
  const byteModules = lowerByteControl(source);
  const program = parsers.babel.parse(source).program.body,
    constants = new Map();
  for (const declaration of program) {
    if (declaration.type !== "VariableDeclaration") continue;
    for (const d of declaration.declarations) {
      if (!["Registers", "State", "Call", "Result", "StatusFlag"].includes(d.id?.name)) continue;
      const n = d.init;
      if (
        n?.type !== "CallExpression" ||
        n.callee.object?.name !== "Object" ||
        n.callee.property?.name !== "freeze" ||
        n.arguments.length !== 1 ||
        n.arguments[0].type !== "ObjectExpression"
      )
        throw Error("Changed CRC constants");
      for (const p of n.arguments[0].properties) {
        if (
          p.type !== "ObjectProperty" ||
          p.computed ||
          p.value.type !== "NumericLiteral" ||
          !Number.isInteger(p.value.value) ||
          p.value.value < 0 ||
          p.value.value > 0xffffffff
        )
          throw Error("Unsupported CRC constant");
        const key = d.id.name + "." + p.key.name;
        if (constants.has(key)) throw Error("Duplicate CRC constant");
        constants.set(key, p.value.value);
      }
    }
  }
  const expected = {
    "State.inIdCrc": 6,
    "State.skipGap_2": 11,
    "State.syncingForIdWait": 2,
    "Call.write": 5,
    "Call.unchanged": 1,
    "Call.seek": 2,
    "Call.readId": 3,
    "Call.read": 4,
    "Registers.internalSeekRetryCount": 1,
    "Registers.internalIdTrack": 12,
    "Registers.internalIdSector": 10,
    "Registers.internalParam_1": 7,
    "Registers.internalParam_2": 6,
    "Registers.internalCommand": 31,
    "Registers.internalGap2Skip": 21,
    "Registers.internalCountMsb": 20,
    "Registers.internalCountLsb": 19,
    "StatusFlag.commandFull": 64,
  };
  for (const [k, v] of Object.entries(expected))
    if (constants.get(k) !== v) throw Error("Changed CRC alias: " + k);
  const classes = program.filter(
    (n) => n.type === "ExportNamedDeclaration" && n.declaration?.id?.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Missing CRC class");
  const methods = classes[0].declaration.body.body;
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
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
      a[0].params.some((p, i) => p.type !== "Identifier" || p.name !== params[i])
    )
      throw Error("Changed CRC method: " + name);
    return a[0];
  };
  const reading = get("_byteCallbackReading", ["dataByte", "clockByte"]),
    check = get("_checkCrc", ["error"]);
  const body = reading.body.body;
  if (
    body.length !== 3 ||
    body[0].type !== "VariableDeclaration" ||
    body[0].kind !== "const" ||
    body[0].declarations.length !== 1 ||
    body[0].declarations[0].id.name !== "command" ||
    member(body[0].declarations[0].init) !== "_internalCommand" ||
    body[1].type !== "IfStatement" ||
    member(body[1].test) !== "_irqCallbacks" ||
    body[1].alternate ||
    body[2].type !== "SwitchStatement" ||
    member(body[2].discriminant) !== "_state"
  )
    throw Error("CRC projection no longer follows the quiet IRQ prelude");
  const selected = body[2].cases.filter(
    (c) =>
      c.test?.type === "MemberExpression" &&
      c.test.object.name === "State" &&
      c.test.property.name === "inIdCrc" &&
      !c.test.computed,
  );
  if (selected.length !== 1) throw Error("Missing unique ID CRC case");
  let serial = 0;
  const declarations = [],
    lines = [],
    reads = new Set(),
    writes = new Set();
  let env = new Map([["dataByte", { code: "{56'd0,byteValue}", kind: "num" }]]),
    stop = "stopped";
  const allocate = (bits = 64) => {
    const n = "local" + serial++;
    declarations.push(`reg[${bits - 1}:0]${n};`);
    return n;
  };
  const number = (code) => ({ code, kind: "num" }),
    boolean = (code) => ({ code, kind: "bool" });
  function literal(v) {
    if (!Number.isInteger(v) || v < 0 || v > 0xffffffff) throw Error("Unsupported CRC Number");
    return number("64'h" + v.toString(16));
  }
  const isRegister = (n) =>
    n?.type === "MemberExpression" && n.computed && member(n.object) === "_regs";
  function address(n) {
    if (
      !isRegister(n) ||
      n.property.type !== "MemberExpression" ||
      n.property.computed ||
      n.property.object.name !== "Registers"
    )
      throw Error("Nonconstant CRC register");
    const a = constants.get("Registers." + n.property.property.name);
    if (![1, 6, 7, 10, 12, 19, 20, 21, 31].includes(a))
      throw Error("CRC register escapes declared support");
    return a;
  }
  function expr(n, pure = false) {
    if (n.type === "NumericLiteral") return literal(n.value);
    if (n.type === "BooleanLiteral") return boolean(n.value ? "64'd1" : "64'd0");
    if (n.type === "Identifier") {
      if (!env.has(n.name)) throw Error("Unbound CRC variable: " + n.name);
      return env.get(n.name);
    }
    if (isRegister(n)) {
      const a = address(n);
      reads.add(a);
      return number("{56'd0,r" + a + "}");
    }
    const f = member(n);
    if (f) {
      const value = {
        _stateCount: "{10'd0,count}",
        _onDiscCrc: "jsInt({32'd0,onCrc})",
        _crc: "{48'd0,crc}",
        _callContext: "{56'd0,callContext}",
      }[f];
      if (!value) throw Error("CRC method reads undeclared state: " + f);
      return number(value);
    }
    if (n.type === "MemberExpression" && !n.computed && n.object.type === "Identifier") {
      const k = n.object.name + "." + n.property.name;
      if (!constants.has(k)) throw Error("Unknown CRC constant");
      return literal(constants.get(k));
    }
    if (n.type === "UpdateExpression" && n.prefix && n.operator === "++") {
      if (pure) throw Error("Impure CRC condition");
      const t = allocate();
      if (member(n.argument) === "_stateCount")
        lines.push(
          t + " = ({10'd0,count} == 64'h20000000000000) ? {10'd0,count} : {10'd0,count}+64'd1;",
          "count = " + t + "[53:0];",
        );
      else {
        const a = address(n.argument);
        if (a !== 1) throw Error("Unexpected CRC increment");
        reads.add(a);
        writes.add(a);
        lines.push(t + " = {56'd0,r1}+64'd1;", "r1 = " + t + "[7:0];");
      }
      return number(t);
    }
    if (n.type === "UnaryExpression" && n.operator === "~") {
      const a = expr(n.argument, pure);
      return number("jsInt(~" + a.code + ")");
    }
    if (n.type === "UnaryExpression" && n.operator === "!") {
      const a = expr(n.argument, pure);
      return boolean("(" + a.code + " == 0)");
    }
    if (n.type === "BinaryExpression") {
      const a = expr(n.left, pure),
        b = expr(n.right, pure);
      if (["===", "!=="].includes(n.operator))
        return boolean(
          a.kind !== b.kind
            ? n.operator === "!=="
              ? "64'd1"
              : "64'd0"
            : "(" + a.code + (n.operator === "===" ? " == " : " != ") + b.code + ")",
        );
      if (["&", "|"].includes(n.operator))
        return number("jsInt(" + a.code + " " + n.operator + " " + b.code + ")");
      if (n.operator === "<<") return number("jsInt(" + a.code + " << (" + b.code + " & 64'd31))");
      throw Error("Unsupported CRC Number operation");
    }
    if (
      n.type === "CallExpression" &&
      member(n.callee) === "_checkCrc" &&
      n.arguments.length === 1
    ) {
      if (pure) throw Error("Impure CRC call");
      const arg = expr(n.arguments[0], true),
        out = allocate(),
        returned = allocate(1),
        oldEnv = env,
        oldStop = stop;
      env = new Map([["error", arg]]);
      stop = returned;
      lines.push(out + " = 0;", returned + " = 0;");
      for (const s of check.body.body) statement(s, out);
      env = oldEnv;
      stop = oldStop;
      return boolean(out);
    }
    throw Error("Unsupported CRC expression: " + n.type);
  }
  function statement(n, result = null) {
    lines.push("if (!" + stop + ") begin");
    if (n.type === "BlockStatement") {
      for (const s of n.body) statement(s, result);
    } else if (n.type === "IfStatement") {
      const t = expr(n.test);
      lines.push("if (" + t.code + " != 0) begin");
      statement(n.consequent, result);
      lines.push("end");
      if (n.alternate) {
        lines.push("else begin");
        statement(n.alternate, result);
        lines.push("end");
      }
    } else if (n.type === "BreakStatement") {
      if (n.label || result !== null) throw Error("Unexpected CRC break");
      lines.push(stop + " = 1;");
    } else if (n.type === "ReturnStatement") {
      if (result === null || !n.argument) throw Error("Unexpected CRC return");
      const v = expr(n.argument);
      if (v.kind !== "bool") throw Error("Nonboolean CRC return");
      lines.push(result + " = " + v.code + ";", stop + " = 1;");
    } else if (n.type === "ExpressionStatement") {
      const e = n.expression;
      if (e.type === "AssignmentExpression" && e.operator === "=") {
        const v = expr(e.right);
        if (member(e.left) === "_onDiscCrc") lines.push("onCrc = " + v.code + ";");
        else {
          const a = address(e.left);
          if (![19, 20, 21].includes(a)) throw Error("Unexpected CRC register write");
          writes.add(a);
          lines.push("r" + a + " = " + v.code + ";");
        }
      } else if (e.type === "CallExpression") {
        const name = member(e.callee);
        if (name === "_logCommand") {
          if (e.arguments.length !== 1 || e.arguments[0].type !== "StringLiteral")
            throw Error("Changed disabled CRC logging");
        } else if (name === "_checkCompletion" && !e.arguments.length)
          lines.push("effectCount = effectCount + 8'd1;", "completed = 1;");
        else if (
          ["_finishCommand", "_doSeek", "_setState"].includes(name) &&
          e.arguments.length === 1
        ) {
          const v = expr(e.arguments[0], true);
          if (v.kind !== "num") throw Error("Non-numeric CRC effect");
          const p = {
            _finishCommand: ["finished", "finishResult"],
            _doSeek: ["seeking", "seekContext"],
            _setState: ["stateWritten", "nextState"],
          }[name];
          lines.push(
            "effectCount = effectCount + 8'd1;",
            p[0] + " = 1;",
            p[1] + " = " + v.code + ";",
          );
        } else throw Error("Unknown CRC effect: " + name);
      } else throw Error("Unsupported CRC expression statement");
    } else throw Error("Unsupported CRC statement: " + n.type);
    lines.push("end");
  }
  for (const n of selected[0].consequent) statement(n);
  const idCases = body[2].cases.filter(
    (c) =>
      c.test?.type === "MemberExpression" &&
      c.test.object.name === "State" &&
      c.test.property.name === "inId" &&
      !c.test.computed,
  );
  if (idCases.length !== 1) throw Error("Missing unique ID body");
  const idBody = idCases[0].consequent,
    decrement = idBody[2]?.expression,
    test = idBody[3];
  function pointerRegister(n) {
    return (
      isRegister(n) &&
      n.property.type === "MemberExpression" &&
      !n.property.computed &&
      n.property.object.name === "Registers" &&
      constants.get("Registers." + n.property.property.name) === 8
    );
  }
  if (
    constants.get("Registers.internalHeaderPointer") !== 8 ||
    idBody.length !== 5 ||
    idBody[2].type !== "ExpressionStatement" ||
    decrement.type !== "UpdateExpression" ||
    decrement.operator !== "--" ||
    decrement.prefix ||
    !pointerRegister(decrement.argument) ||
    test.type !== "IfStatement" ||
    test.alternate ||
    test.consequent.type !== "BlockStatement" ||
    test.consequent.body.length !== 3 ||
    idBody[4].type !== "BreakStatement" ||
    idBody[4].label
  )
    throw Error("Changed default ID decrement/stop tail");
  const actions = test.consequent.body;
  if (actions.some((n) => n.type !== "ExpressionStatement"))
    throw Error("Unsupported ID stop effect");
  const zero = actions[0].expression,
    stopCall = actions[1].expression,
    stateCall = actions[2].expression;
  if (
    zero.type !== "AssignmentExpression" ||
    zero.operator !== "=" ||
    member(zero.left) !== "_onDiscCrc" ||
    zero.right.type !== "NumericLiteral" ||
    zero.right.value !== 0 ||
    stopCall.type !== "CallExpression" ||
    member(stopCall.callee) !== "_stopIrqCallbacks" ||
    stopCall.arguments.length ||
    stateCall.type !== "CallExpression" ||
    member(stateCall.callee) !== "_setState" ||
    stateCall.arguments.length !== 1 ||
    stateCall.arguments[0].type !== "MemberExpression" ||
    stateCall.arguments[0].computed ||
    stateCall.arguments[0].object.name !== "State" ||
    constants.get("State." + stateCall.arguments[0].property.name) !== 6
  )
    throw Error("Changed ID CRC entry effects");
  function tailExpression(n) {
    if (n.type === "NumericLiteral") return literal(n.value);
    if (pointerRegister(n)) return number("{56'd0,followingPointer}");
    if (n.type === "BinaryExpression") {
      const a = tailExpression(n.left),
        b = tailExpression(n.right);
      if (n.operator === "&") return number("jsInt(" + a.code + " & " + b.code + ")");
      if (n.operator === "===") {
        if (a.kind !== b.kind) return boolean("64'd0");
        return boolean("(" + a.code + " == " + b.code + ")");
      }
    }
    throw Error("Unsupported ID stop predicate");
  }
  const stopPredicate = tailExpression(test.test);
  if (stopPredicate.kind !== "bool") throw Error("Nonboolean ID stop test");
  const readBody = get("_readRegister", ["reg"]).body.body,
    maskRead = readBody[0]?.expression,
    bankRead = readBody[1];
  const bankReturn = bankRead?.consequent?.body?.[0];
  if (
    readBody.length < 2 ||
    readBody[0].type !== "ExpressionStatement" ||
    maskRead.type !== "AssignmentExpression" ||
    maskRead.operator !== "&=" ||
    maskRead.left.type !== "Identifier" ||
    maskRead.left.name !== "reg" ||
    maskRead.right.type !== "NumericLiteral" ||
    maskRead.right.value !== 63 ||
    bankRead.type !== "IfStatement" ||
    bankRead.alternate ||
    bankRead.test.type !== "BinaryExpression" ||
    bankRead.test.operator !== "<" ||
    bankRead.test.left.type !== "Identifier" ||
    bankRead.test.left.name !== "reg" ||
    bankRead.test.right.type !== "MemberExpression" ||
    bankRead.test.right.computed ||
    bankRead.test.right.object.name !== "IntelFdc" ||
    bankRead.test.right.property.name !== "NumRegisters" ||
    bankRead.consequent.type !== "BlockStatement" ||
    bankRead.consequent.body.length !== 1 ||
    bankReturn.type !== "ReturnStatement" ||
    !isRegister(bankReturn.argument) ||
    bankReturn.argument.property.type !== "Identifier" ||
    bankReturn.argument.property.name !== "reg"
  )
    throw Error("Changed direct typed register read");
  const readModule =
    "module FdcStatusReadSource(input[7:0]status,output[7:0]value);assign value=status;endmodule\n";
  const acceptance = get("_commandWritten", ["command"]).body.body;
  const lowerFull = acceptance[4]?.expression;
  if (
    lowerFull?.type !== "CallExpression" ||
    member(lowerFull.callee) !== "_statusLower" ||
    lowerFull.arguments.length !== 1 ||
    lowerFull.arguments[0].type !== "MemberExpression" ||
    lowerFull.arguments[0].computed ||
    lowerFull.arguments[0].object.name !== "StatusFlag" ||
    lowerFull.arguments[0].property.name !== "commandFull"
  )
    throw Error("Changed command-full acceptance prefix");
  const commandModule = `module FdcCommandStatusSource(input[7:0]status,output[7:0]value);assign value=status & 8'd${255 ^ constants.get("StatusFlag.commandFull")};endmodule\n`;
  const stopBody = get("_stopIrqCallbacks", []).body.body;
  const stopExpression = stopBody[0]?.expression,
    stopLeft = stopExpression?.left;
  if (
    stopBody.length !== 1 ||
    stopBody[0].type !== "ExpressionStatement" ||
    stopExpression.type !== "AssignmentExpression" ||
    stopExpression.operator !== "&=" ||
    !isRegister(stopLeft) ||
    stopLeft.property.type !== "MemberExpression" ||
    stopLeft.property.computed ||
    stopLeft.property.object.name !== "Registers" ||
    constants.get("Registers." + stopLeft.property.property.name) !== 23
  )
    throw Error("Changed IRQ-stop status assignment");
  function staticMask(n) {
    if (n.type === "NumericLiteral") return;
    if (n.type === "UnaryExpression" && n.operator === "~") return staticMask(n.argument);
    if (n.type === "BinaryExpression" && ["&", "|", "<<"].includes(n.operator)) {
      staticMask(n.left);
      staticMask(n.right);
      return;
    }
    throw Error("Nonstatic IRQ-stop mask");
  }
  staticMask(stopExpression.right);
  const mask = expr(stopExpression.right, true);
  if (mask.kind !== "num") throw Error("Non-number IRQ-stop mask");
  const stopModule = `module FdcHeaderStopSource(input[7:0]status,pointer,output[7:0]followingStatus,followingPointer,output stopping);
function[63:0]jsInt(input[63:0]v);begin jsInt={{32{v[31]}},v[31:0]};end endfunction
wire[63:0]mask=${mask.code};assign followingPointer=pointer-8'd1;assign stopping=${stopPredicate.code};assign followingStatus=stopping?(status & mask[7:0]):status;
endmodule
`;
  const regs = Array.from({ length: 32 }, (_, a) => "r" + a);
  return (
    byteModules +
    readModule +
    commandModule +
    stopModule +
    `module FdcIdCrcSource(input[255:0]registers,input[53:0]stateCount,input[31:0]onDiscCrc,input[15:0]crc,input[7:0]byteValue,callContext,output[255:0]followingRegisters,output[53:0]followingCount,output[31:0]followingCrc,output reg finished,completed,seeking,stateWritten,output reg[7:0]finishResult,seekContext,nextState,effectCount);
reg[7:0]${regs.join(",")};reg[53:0]count;reg[31:0]onCrc;reg stopped;
${declarations.join("\n")}
function[63:0]jsInt(input[63:0]v);begin jsInt={{32{v[31]}},v[31:0]};end endfunction
assign followingRegisters={${[...regs].reverse().join(",")}};assign followingCount=count;assign followingCrc=onCrc;
always @(*)begin
${regs.map((r, a) => r + " = registers[" + a * 8 + " +: 8];").join("\n")}
count=stateCount;onCrc=onDiscCrc;stopped=0;finished=0;completed=0;seeking=0;stateWritten=0;finishResult=0;seekContext=0;nextState=0;effectCount=0;
${declarations.map((d) => d.match(/(local\d+);$/)[1] + "=0;").join("\n")}
${lines.join("\n")}
end
endmodule
`
  );
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(
    directory + "/source.v",
    lowerIdCrc(fs.readFileSync(root + "/src/intel-fdc.js", "utf8")),
  );
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const f = Object.create(IntelFdc.prototype);
  f._regs = new Uint8Array(32);
  let effects = 0,
    flags = 0,
    result = 0,
    seek = 0,
    state = 0;
  f._finishCommand = (code) => {
    effects++;
    flags |= 1;
    result = code;
  };
  f._checkCompletion = () => {
    effects++;
    flags |= 2;
  };
  f._doSeek = (context) => {
    effects++;
    flags |= 4;
    seek = context;
  };
  f._setState = (value) => {
    effects++;
    flags |= 8;
    state = value;
  };
  f._logCommand = () => {};
  const total = 1048576 + 131072 + 10240,
    bytes = Buffer.alloc(total * 96);
  let index = 0;
  function sample(
    command,
    retry,
    good,
    mismatch,
    match,
    context,
    count = 1,
    onCrc = 0x12,
    byte = 0x34,
  ) {
    const p = index++ * 96;
    for (let a = 0; a < 32; a++) f._regs[a] = (17 * a + command + retry) & 255;
    f._regs[23] &= 0xcf;
    f._regs[31] = command;
    f._regs[1] = retry;
    f._regs[12] = 0x5a;
    f._regs[7] = mismatch ? 0x5b : 0x5a;
    f._regs[10] = 0xab;
    f._regs[6] = match ? 0xab : 0xac;
    f._state = 6;
    f._stateCount = count;
    f._onDiscCrc = onCrc | 0;
    f._crc = (((onCrc << 8) | byte) & 0xffff) ^ (good ? 0 : 1);
    f._callContext = context;
    bytes.set(f._regs, p);
    bytes.writeBigUInt64LE(BigInt(count), p + 32);
    bytes.writeInt32LE(onCrc | 0, p + 40);
    bytes.writeUInt16LE(f._crc, p + 44);
    bytes[p + 46] = byte;
    bytes[p + 47] = context;
    effects = flags = result = seek = state = 0;
    f._byteCallbackReading(byte, 255);
    bytes.set(f._regs, p + 48);
    bytes.writeBigUInt64LE(BigInt(f._stateCount), p + 80);
    bytes.writeInt32LE(f._onDiscCrc, p + 88);
    assert.ok(effects <= 1, "CRC projection requires one delegated API call per decision");
    bytes[p + 92] = flags | (effects << 4);
    bytes[p + 93] = result;
    bytes[p + 94] = seek;
    bytes[p + 95] = state;
  }
  for (let command = 0; command < 256; command++)
    for (let retry = 0; retry < 256; retry++)
      for (let shape = 0; shape < 16; shape++)
        sample(command, retry, !!(shape & 1), !!(shape & 2), !!(shape & 4), shape & 8 ? 5 : 4);
  for (let high = 0; high < 256; high++)
    for (let byte = 0; byte < 256; byte++)
      for (const good of [false, true]) sample(24, 0, good, false, true, 4, 1, high, byte);
  for (const count of [0, 2, 3, 2 ** 53 - 1, 2 ** 53])
    for (let command = 0; command < 256; command++)
      for (const context of [4, 5])
        for (const crc of [0, 0x7fffffff, 0x80000000, 0xffffffff])
          sample(command, 255, true, false, true, context, count, crc, 255);
  assert.equal(index, total);
  fs.writeFileSync(directory + "/original.bin", bytes);

  const statusValues = Buffer.alloc(512);
  for (let status = 0; status < 256; status++) {
    f._regs[23] = status;
    statusValues[2 * status] = f._readRegister(23);
    IntelFdc.prototype._statusLower.call(f, 64);
    statusValues[2 * status + 1] = f._regs[23];
  }
  fs.writeFileSync(directory + "/status.bin", statusValues);
  const header = Buffer.alloc(4 * 256 * 5);
  let h = 0;
  const originalStop = IntelFdc.prototype._stopIrqCallbacks;
  for (const pointer of [9, 10, 11, 12]) {
    f._regs.fill(0);
    f._regs[8] = pointer;
    f._state = 5;
    f._stateCount = 0;
    f._crc = 0;
    f._onDiscCrc = 0x123456;
    let called = false;
    f._stopIrqCallbacks = () => {
      called = true;
      originalStop.call(f);
    };
    effects = flags = result = seek = state = 0;
    f._byteCallbackReading(0xa5, 255);
    const nextPointer = f._regs[8];
    assert.equal(flags, called ? 8 : 0);
    assert.equal(state, called ? 6 : 0);
    for (let status = 0; status < 256; status++) {
      f._regs[23] = status;
      if (called) originalStop.call(f);
      header.set([pointer, status, nextPointer, f._regs[23], +called], h * 5);
      h++;
    }
  }
  assert.equal(h, 1024);
  fs.writeFileSync(directory + "/header.bin", header);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({
      passed: true,
      crc_cases: total,
      header_cases: h,
      status_cases: 256,
      registers: 32,
      limits:
        "CRC body and default ID stop-tail projections retain explicit completion, seek, state-change and finish API boundaries; IRQ getters and data-loss eligibility are lowered separately. Header stop inputs follow a distinct destination write with pointer in 9..12. Number counts are dense integers through 2^53.",
    }) + "\n",
  );
  console.log(total, "original ID CRC API-boundary records;", h, "default ID stop records");
}
