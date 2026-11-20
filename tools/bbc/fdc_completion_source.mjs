import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerByteControl } from "./fdc_byte_source.mjs";

export function lowerCompletion(source) {
  lowerByteControl(source);
  const program = parsers.babel.parse(source).program.body;
  const constants = new Map();
  for (const n of program)
    if (n.type === "VariableDeclaration")
      for (const d of n.declarations) {
        if (!["Registers", "DriveOut", "Result", "Command", "ParamAccept"].includes(d.id?.name))
          continue;
        if (
          d.init?.type !== "CallExpression" ||
          d.init.callee?.object?.name !== "Object" ||
          d.init.callee.property?.name !== "freeze" ||
          d.init.arguments.length !== 1 ||
          d.init.arguments[0].type !== "ObjectExpression"
        )
          throw Error("Changed completion constants");
        for (const p of d.init.arguments[0].properties) {
          if (
            p.type !== "ObjectProperty" ||
            p.computed ||
            p.value.type !== "NumericLiteral" ||
            !Number.isInteger(p.value.value) ||
            p.value.value < 0 ||
            p.value.value > 255
          )
            throw Error("Nonbyte completion constant");
          const key = d.id.name + "." + p.key.name;
          if (constants.has(key)) throw Error("Duplicate constant");
          constants.set(key, p.value.value);
        }
      }
  for (const [k, v] of Object.entries({
    "Registers.internalParam_3": 5,
    "Registers.internalParam_2": 6,
    "Registers.internalParam_4": 4,
    "Registers.internalPointer": 0,
    "Registers.internalParamCount": 1,
    "Registers.internalParam_1": 7,
    "Command.specify": 13,
    "ParamAccept.specify": 2,
  }))
    if (constants.get(k) !== v) throw Error("Changed completion alias " + k);
  const classes = program.filter(
    (n) =>
      n.type === "ExportNamedDeclaration" &&
      n.declaration?.type === "ClassDeclaration" &&
      n.declaration.id.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Changed controller class");
  const methods = classes[0].declaration.body.body;
  const get = (name) => {
    const a = methods.filter((n) => n.key?.name === name);
    if (
      a.length !== 1 ||
      a[0].kind !== "method" ||
      a[0].static ||
      a[0].computed ||
      a[0].async ||
      a[0].generator ||
      a[0].params.length
    )
      throw Error("Changed completion method " + name);
    return a[0].body.body;
  };
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
  const constant = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "Identifier"
      ? constants.get(n.object.name + "." + n.property.name)
      : undefined;
  const register = (n) =>
    n?.type === "MemberExpression" && n.computed && member(n.object) === "_regs"
      ? constant(n.property)
      : undefined;
  const names = { 5: "followingCount", 6: "followingSector", 4: "stepByte" };
  const lines = [],
    calls = new Map();
  const called = (n) => calls.set(n, (calls.get(n) || 0) + 1);
  function expr(n) {
    if (n.type === "NumericLiteral" && Number.isInteger(n.value) && n.value >= 0 && n.value <= 255)
      return "32'd" + n.value;
    const c = constant(n);
    if (c !== undefined) return "32'd" + c;
    const r = register(n);
    if (Object.hasOwn(names, r)) return "{24'b0," + names[r] + "}";
    if (
      n.type === "UpdateExpression" &&
      n.operator === "--" &&
      n.prefix &&
      register(n.argument) === 5
    ) {
      called("decrement");
      lines.push(
        "decrementValue=$signed({24'b0,followingCount})-32'sd1;followingCount=decrementValue[7:0];",
      );
      return "decrementValue";
    }
    if (n.type === "BinaryExpression" && ["&", "===", "+"].includes(n.operator))
      return (
        "(" +
        expr(n.left) +
        (n.operator === "===" ? " == " : " " + n.operator + " ") +
        expr(n.right) +
        ")"
      );
    throw Error("Unsupported completion expression " + n.type);
  }
  function statement(n) {
    if (n.type === "BlockStatement") {
      n.body.forEach(statement);
      return;
    }
    if (n.type === "IfStatement" && n.alternate) {
      const test = expr(n.test);
      lines.push("if(" + test + ")begin");
      statement(n.consequent);
      lines.push("end else begin");
      statement(n.alternate);
      lines.push("end");
      return;
    }
    if (n.type !== "ExpressionStatement") throw Error("Unsupported completion statement " + n.type);
    const e = n.expression;
    if (e.type === "AssignmentExpression" && e.operator === "+=" && register(e.left) === 6) {
      const value = expr(e.right);
      called("increment");
      lines.push("followingSector=followingSector+" + value + ";");
      return;
    }
    if (e.type !== "CallExpression") throw Error("Unsupported completion effect");
    const name = member(e.callee);
    called(name);
    const emit = (id) => lines.push("callOrder=(callOrder<<4)|16'd" + id + ";");
    if (name === "_driveOutLower" && e.arguments.length === 1) {
      lines.push("driveMask=" + expr(e.arguments[0]) + ";");
      emit(2);
      return;
    }
    if (name === "_clearCallbacks" && !e.arguments.length) {
      emit(3);
      return;
    }
    if (name === "_finishCommand" && e.arguments.length === 1) {
      lines.push("finishResult=" + expr(e.arguments[0]) + ";");
      emit(4);
      return;
    }
    if (name === "_doCommandDispatch" && !e.arguments.length) {
      emit(5);
      return;
    }
    throw Error("Unsupported completion API " + name);
  }
  const body = get("_checkCompletion"),
    first = body[0];
  if (
    body.length !== 4 ||
    first.type !== "IfStatement" ||
    first.alternate ||
    first.consequent.type !== "ReturnStatement" ||
    first.consequent.argument ||
    first.test.type !== "UnaryExpression" ||
    first.test.operator !== "!" ||
    first.test.argument.type !== "CallExpression" ||
    member(first.test.argument.callee) !== "_checkDriveReady" ||
    first.test.argument.arguments.length
  )
    throw Error("Changed completion ready guard");
  body.slice(1).forEach(statement);
  for (const n of [
    "_driveOutLower",
    "_clearCallbacks",
    "_finishCommand",
    "_doCommandDispatch",
    "decrement",
    "increment",
  ])
    if (calls.get(n) !== 1) throw Error("Changed completion call inventory " + n);
  const dispatch = get("_doCommandDispatch");
  if (
    dispatch.length !== 2 ||
    dispatch[0].type !== "VariableDeclaration" ||
    dispatch[0].kind !== "const" ||
    dispatch[0].declarations.length !== 1 ||
    dispatch[0].declarations[0].id.name !== "command" ||
    member(dispatch[0].declarations[0].init) !== "_internalCommand" ||
    dispatch[1].type !== "SwitchStatement" ||
    dispatch[1].discriminant.name !== "command"
  )
    throw Error("Changed dispatch prelude");
  const cases = dispatch[1].cases.filter(
    (n) => constant(n.test) === constants.get("Command.specify"),
  );
  if (
    cases.length !== 1 ||
    cases[0].consequent.length !== 4 ||
    cases[0].consequent[3].type !== "BreakStatement"
  )
    throw Error("Changed SPECIFY case");
  const spec = [];
  for (const [i, n] of cases[0].consequent.slice(0, 3).entries()) {
    const e = n.expression;
    if (
      n.type !== "ExpressionStatement" ||
      e?.type !== "AssignmentExpression" ||
      e.operator !== "="
    )
      throw Error("Changed SPECIFY effect");
    if (i === 0 && register(e.left) === 0 && register(e.right) === 7)
      spec.push("pointer=parameterByte;");
    else if (
      i === 1 &&
      register(e.left) === 1 &&
      e.right.type === "NumericLiteral" &&
      e.right.value === 3
    )
      spec.push("parameterCount=8'd3;");
    else if (
      i === 2 &&
      member(e.left) === "_paramCallback" &&
      constant(e.right) === constants.get("ParamAccept.specify")
    )
      spec.push("callback=8'd" + constant(e.right) + ";");
    else throw Error("Changed SPECIFY assignment");
  }
  return (
    "module FdcCompletionSource(input ready,input[7:0]countByte,sectorByte,stepByte,output reg[7:0]followingCount,followingSector,driveMask,finishResult,output reg[15:0]callOrder);reg signed[31:0]decrementValue;always @* begin followingCount=countByte;followingSector=sectorByte;driveMask=0;finishResult=0;callOrder=1;decrementValue=0;if(ready)begin\n" +
    lines.join("\n") +
    "\nend end endmodule\nmodule FdcCompletionSpecifySource(input[7:0]parameterByte,output reg[7:0]pointer,parameterCount,callback);always @* begin\n" +
    spec.join("\n") +
    "\nend endmodule\n"
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  fs.mkdirSync(directory, { recursive: true });
  const source = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  fs.writeFileSync(directory + "/source.v", lowerCompletion(source));
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const fdc = Object.create(IntelFdc.prototype);
  fdc._regs = new Uint8Array(32);
  let ready = 0,
    callOrder = 0,
    mask = 0,
    result = 0;
  fdc._checkDriveReady = () => {
    callOrder = (callOrder << 4) | 1;
    return !!ready;
  };
  fdc._driveOutLower = (x) => {
    mask = x;
    callOrder = (callOrder << 4) | 2;
  };
  fdc._clearCallbacks = () => {
    callOrder = (callOrder << 4) | 3;
  };
  fdc._finishCommand = (x) => {
    result = x;
    callOrder = (callOrder << 4) | 4;
  };
  fdc._doCommandDispatch = () => {
    callOrder = (callOrder << 4) | 5;
  };
  const rows = Buffer.alloc(33554432 * 6);
  let p = 0;
  for (ready = 0; ready < 2; ready++)
    for (let count = 0; count < 256; count++)
      for (let sector = 0; sector < 256; sector++)
        for (let step = 0; step < 256; step++) {
          fdc._regs[5] = count;
          fdc._regs[6] = sector;
          fdc._regs[4] = step;
          callOrder = mask = result = 0;
          IntelFdc.prototype._checkCompletion.call(fdc);
          for (const v of [
            fdc._regs[5],
            fdc._regs[6],
            mask,
            result,
            callOrder & 255,
            callOrder >>> 8,
          ])
            rows[p++] = v;
        }
  fs.writeFileSync(directory + "/original.bin", rows);
  const specify = Buffer.alloc(256 * 3);
  p = 0;
  for (let parameter = 0; parameter < 256; parameter++) {
    fdc._regs[31] = 52;
    fdc._regs[7] = parameter;
    fdc._regs[0] = 255;
    fdc._regs[1] = 255;
    fdc._paramCallback = 255;
    IntelFdc.prototype._doCommandDispatch.call(fdc);
    for (const v of [fdc._regs[0], fdc._regs[1], fdc._paramCallback]) specify[p++] = v;
  }
  fs.writeFileSync(directory + "/specify.bin", specify);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({
      passed: true,
      completion_cases: 33554432,
      specify_cases: 256,
      registers: 32,
    }) + "\n",
  );
}
