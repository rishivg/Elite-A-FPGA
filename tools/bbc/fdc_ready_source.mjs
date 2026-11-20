import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerByteControl } from "./fdc_byte_source.mjs";

export function lowerReady(source) {
  lowerByteControl(source);
  const body = parsers.babel.parse(source).program.body,
    constants = new Map();
  for (const n of body)
    if (n.type === "VariableDeclaration")
      for (const d of n.declarations) {
        if (!["Registers", "DriveOut", "Result"].includes(d.id?.name)) continue;
        if (
          d.init?.type !== "CallExpression" ||
          d.init.callee?.object?.name !== "Object" ||
          d.init.callee.property?.name !== "freeze" ||
          d.init.arguments.length !== 1 ||
          d.init.arguments[0].type !== "ObjectExpression"
        )
          throw Error("Changed ready constants");
        for (const p of d.init.arguments[0].properties) {
          if (
            p.type !== "ObjectProperty" ||
            p.computed ||
            p.value.type !== "NumericLiteral" ||
            !Number.isInteger(p.value.value) ||
            p.value.value < 0 ||
            p.value.value > 255
          )
            throw Error("Nonbyte ready constant");
          const k = d.id.name + "." + p.key.name;
          if (constants.has(k)) throw Error("Duplicate constant");
          constants.set(k, p.value.value);
        }
      }
  for (const [k, v] of Object.entries({
    "Registers.internalDriveInCopy": 21,
    "Registers.internalDriveInLatched": 27,
    "DriveOut.select_1": 128,
    "Result.driveNotReady": 16,
  }))
    if (constants.get(k) !== v) throw Error("Changed ready alias " + k);
  const classes = body.filter(
    (n) =>
      n.type === "ExportNamedDeclaration" &&
      n.declaration?.type === "ClassDeclaration" &&
      n.declaration.id.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Changed controller class");
  const methods = classes[0].declaration.body.body;
  function get(name, params = []) {
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
      throw Error("Changed ready method " + name);
    return a[0].body.body;
  }
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
  const locals = new Map(),
    lines = [],
    calls = new Map();
  let readReturns = 0,
    readyReturns = 0;
  function expression(n) {
    if (n.type === "NumericLiteral" && Number.isInteger(n.value) && n.value >= 0 && n.value <= 255)
      return { kind: "byte", code: "8'd" + n.value };
    if (n.type === "BooleanLiteral") return { kind: "bool", code: n.value ? "1'b1" : "1'b0" };
    const c = constant(n);
    if (c !== undefined) return { kind: "byte", code: "8'd" + c };
    if (n.type === "Identifier" && locals.has(n.name)) return { kind: "byte", code: n.name };
    if (member(n) === "_driveIn") return { kind: "byte", code: "pinsByte" };
    if (member(n) === "_driveOut") return { kind: "byte", code: "driveByte" };
    const r = register(n);
    if (r === 21 || r === 27)
      return { kind: "byte", code: r === 21 ? "followingCopy" : "followingLatch" };
    if (n.type === "BinaryExpression" && ["&", "|"].includes(n.operator)) {
      const a = expression(n.left),
        b = expression(n.right);
      if (a.kind !== "byte" || b.kind !== "byte") throw Error("Nonnumeric ready bit operation");
      return { kind: "byte", code: "(" + a.code + " " + n.operator + " " + b.code + ")" };
    }
    if (n.type === "UnaryExpression" && n.operator === "!" && n.prefix) {
      const a = expression(n.argument);
      return { kind: "bool", code: "(!" + a.code + ")" };
    }
    if (n.type === "ConditionalExpression") {
      const t = expression(n.test),
        a = expression(n.consequent),
        b = expression(n.alternate);
      if (a.kind !== "byte" || b.kind !== "byte") throw Error("Nonbyte ready selection");
      return { kind: "byte", code: "(" + t.code + "?" + a.code + ":" + b.code + ")" };
    }
    throw Error("Unsupported ready expression " + n.type);
  }
  function statement(n, kind) {
    if (n.type === "BlockStatement") {
      n.body.forEach((x) => statement(x, kind));
      return;
    }
    if (n.type === "VariableDeclaration") {
      if (n.declarations.length !== 1 || !["let", "const"].includes(n.kind))
        throw Error("Changed ready local");
      const d = n.declarations[0];
      if (
        d.id.type !== "Identifier" ||
        !["driveIn", "mask"].includes(d.id.name) ||
        locals.has(d.id.name)
      )
        throw Error("Unexpected ready local");
      const v = expression(d.init);
      if (v.kind !== "byte") throw Error("Nonbyte ready local");
      locals.set(d.id.name, true);
      lines.push(d.id.name + "=" + v.code + ";");
      return;
    }
    if (n.type === "IfStatement" && !n.alternate) {
      const t = expression(n.test);
      if (t.kind !== "bool") throw Error("Nonboolean ready condition");
      lines.push("if(" + t.code + ")begin");
      statement(n.consequent, kind);
      lines.push("end");
      return;
    }
    if (n.type === "ReturnStatement") {
      const v = expression(n.argument);
      if (kind === "read" && v.kind === "byte") {
        readReturns++;
        lines.push("statusValue=" + v.code + ";");
        return;
      }
      if (kind === "ready" && v.kind === "bool") {
        readyReturns++;
        lines.push("if(!returned)begin ready=" + v.code + ";returned=1'b1;end");
        return;
      }
      throw Error("Changed ready return type");
    }
    if (n.type !== "ExpressionStatement") throw Error("Unsupported ready statement " + n.type);
    const e = n.expression;
    if (e.type === "AssignmentExpression" && ["=", "|=", "&="].includes(e.operator)) {
      const r = register(e.left),
        name =
          r === 21
            ? "followingCopy"
            : r === 27
              ? "followingLatch"
              : e.left.type === "Identifier" && locals.has(e.left.name)
                ? e.left.name
                : null;
      if (!name) throw Error("Unknown ready assignment");
      const v = expression(e.right);
      if (v.kind !== "byte") throw Error("Nonbyte ready assignment");
      lines.push(name + "=" + (e.operator === "=" ? v.code : name + e.operator[0] + v.code) + ";");
      return;
    }
    if (e.type === "CallExpression") {
      const name = member(e.callee);
      calls.set(name, (calls.get(name) || 0) + 1);
      if (kind === "ready" && name === "_doReadDriveStatus" && !e.arguments.length) return;
      if (kind === "ready" && name === "_finishCommand" && e.arguments.length === 1) {
        const v = expression(e.arguments[0]);
        if (v.kind !== "byte") throw Error("Nonbyte ready failure");
        lines.push("finished=1'b1;finishResult=" + v.code + ";");
        return;
      }
    }
    throw Error("Unsupported ready effect");
  }
  get("_doReadDriveStatus").forEach((n) => statement(n, "read"));
  const readyBody = get("_checkDriveReady");
  if (
    readyBody.length !== 4 ||
    readyBody[0].expression?.type !== "CallExpression" ||
    member(readyBody[0].expression.callee) !== "_doReadDriveStatus"
  )
    throw Error("Changed ready sampling order");
  readyBody.forEach((n) => statement(n, "ready"));
  if (
    readReturns !== 1 ||
    readyReturns !== 2 ||
    calls.get("_doReadDriveStatus") !== 1 ||
    calls.get("_finishCommand") !== 1 ||
    locals.size !== 2
  )
    throw Error("Changed ready effect inventory");
  // Direct bank writes return before MMIO; reject any added effects in that prefix.
  function clean(n) {
    if (Array.isArray(n)) return n.map(clean);
    if (n && typeof n === "object")
      return Object.fromEntries(
        Object.entries(n)
          .filter(
            ([k]) =>
              ![
                "start",
                "end",
                "loc",
                "comments",
                "leadingComments",
                "trailingComments",
                "innerComments",
                "extra",
              ].includes(k),
          )
          .map(([k, v]) => [k, clean(v)]),
      );
    return n;
  }
  const statusFrame = parsers.babel.parse(
    "function f(){let driveIn=this._driveIn;this._regs[Registers.internalDriveInCopy]=driveIn;this._regs[Registers.internalDriveInLatched]|=0xbb;driveIn &= this._regs[Registers.internalDriveInLatched];this._regs[Registers.internalDriveInLatched]=driveIn;return driveIn;}",
  ).program.body[0].body.body;
  if (JSON.stringify(clean(get("_doReadDriveStatus"))) !== JSON.stringify(clean(statusFrame)))
    throw Error("Changed drive-status read-input frame");
  const expected = parsers.babel.parse(
    "function f(reg,val){reg &= 0x3f;if(reg<IntelFdc.NumRegisters){this._regs[reg]=val;return;}}",
  ).program.body[0].body.body;
  if (
    JSON.stringify(clean(get("_writeRegister", ["reg", "val"]).slice(0, 2))) !==
    JSON.stringify(clean(expected))
  )
    throw Error("Changed ready register-write prefix");
  return (
    "module FdcReadySource(input[7:0]pinsByte,driveByte,oldLatch,output reg[7:0]followingCopy,followingLatch,statusValue,finishResult,output reg ready,finished);reg[7:0]driveIn,mask;reg returned;always @* begin followingCopy=0;followingLatch=oldLatch;statusValue=0;finishResult=0;ready=0;finished=0;returned=0;driveIn=0;mask=0;\n" +
    lines.join("\n") +
    "\nend endmodule\nmodule FdcReadyWriteSource(input[7:0]value,output[7:0]followingLatch);assign followingLatch=value;endmodule\n"
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  fs.mkdirSync(directory, { recursive: true });
  const text = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  fs.writeFileSync(directory + "/source.v", lowerReady(text));
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const fdc = Object.create(IntelFdc.prototype);
  fdc._regs = new Uint8Array(32);
  let pins = 0,
    finished = 0,
    result = 0,
    status = 0;
  Object.defineProperty(fdc, "_driveIn", { get: () => pins });
  fdc._finishCommand = (x) => {
    finished = 1;
    result = x;
  };
  fdc._doReadDriveStatus = () => {
    status = IntelFdc.prototype._doReadDriveStatus.call(fdc);
    return status;
  };
  const bytes = Buffer.alloc(16777216 * 6);
  let p = 0;
  for (pins = 0; pins < 256; pins++)
    for (let drive = 0; drive < 256; drive++)
      for (let latch = 0; latch < 256; latch++) {
        fdc._driveOut = drive;
        fdc._regs[27] = latch;
        fdc._regs[21] = 0;
        finished = result = 0;
        const ready = IntelFdc.prototype._checkDriveReady.call(fdc);
        for (const v of [fdc._regs[21], fdc._regs[27], +ready, finished, result, status])
          bytes[p++] = v;
      }
  fs.writeFileSync(directory + "/original.bin", bytes);
  const writes = Buffer.alloc(256);
  for (let value = 0; value < 256; value++) {
    IntelFdc.prototype._writeRegister.call(fdc, 27, value);
    writes[value] = fdc._regs[27];
  }
  fs.writeFileSync(directory + "/writes.bin", writes);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({ passed: true, ready_cases: 16777216, write_cases: 256, registers: 32 }) + "\n",
  );
}
