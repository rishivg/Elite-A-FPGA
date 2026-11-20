import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
import { lowerReady } from "./fdc_ready_source.mjs";
export function lowerGap(source) {
  lowerReady(source);
  const program = parsers.babel.parse(source).program.body,
    constants = new Map();
  for (const n of program)
    if (n.type === "VariableDeclaration")
      for (const d of n.declarations) {
        if (!["Registers", "State", "Call"].includes(d.id?.name)) continue;
        if (
          d.init?.type !== "CallExpression" ||
          d.init.callee?.object?.name !== "Object" ||
          d.init.callee.property?.name !== "freeze" ||
          d.init.arguments.length !== 1 ||
          d.init.arguments[0].type !== "ObjectExpression"
        )
          throw Error("Changed gap constants");
        for (const p of d.init.arguments[0].properties) {
          if (
            p.type !== "ObjectProperty" ||
            p.computed ||
            p.value.type !== "NumericLiteral" ||
            !Number.isInteger(p.value.value) ||
            p.value.value < 0 ||
            p.value.value > 255
          )
            throw Error("Nonbyte gap constant");
          const k = d.id.name + "." + p.key.name;
          if (constants.has(k)) throw Error("Duplicate constant");
          constants.set(k, p.value.value);
        }
      }
  for (const [k, v] of Object.entries({
    "Registers.internalGap2Skip": 21,
    "State.skipGap_2": 11,
    "State.syncingForData": 7,
    "Call.read": 4,
    "Call.write": 5,
  }))
    if (constants.get(k) !== v) throw Error("Changed gap alias " + k);
  const classes = program.filter(
    (n) =>
      n.type === "ExportNamedDeclaration" &&
      n.declaration?.type === "ClassDeclaration" &&
      n.declaration.id.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Changed controller class");
  const methods = classes[0].declaration.body.body,
    method = methods.filter((n) => n.key?.name === "_byteCallbackReading");
  if (
    method.length !== 1 ||
    method[0].kind !== "method" ||
    method[0].static ||
    method[0].computed ||
    method[0].async ||
    method[0].generator ||
    method[0].params.length !== 2 ||
    method[0].params.some(
      (n, i) => n.type !== "Identifier" || n.name !== ["dataByte", "clockByte"][i],
    )
  )
    throw Error("Changed gap callback");
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
  const constant = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "Identifier"
      ? constants.get(n.object.name + "." + n.property.name)
      : undefined;
  const reg = (n) =>
    n?.type === "MemberExpression" && n.computed && member(n.object) === "_regs"
      ? constant(n.property)
      : undefined;
  const body = method[0].body.body;
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
    throw Error("Changed quiet gap prelude");
  const label = (n) =>
    n?.type === "NumericLiteral" && Number.isInteger(n.value) && n.value >= 0 && n.value <= 255
      ? n.value
      : constant(n);
  if (body[2].cases.some((n) => n.test !== null && label(n.test) === undefined))
    throw Error("Effectful or unknown gap case label");
  const selected = body[2].cases.filter((n) => label(n.test) === 11);
  if (selected.length !== 1 || selected[0].consequent.length !== 3) throw Error("Changed gap case");
  const lines = [],
    calls = new Map();
  let decrements = 0,
    breaks = 0,
    traps = 0;
  function expression(n) {
    if (n.type === "NumericLiteral" && Number.isInteger(n.value) && n.value >= 0 && n.value <= 255)
      return { kind: "byte", code: "8'd" + n.value };
    const c = constant(n);
    if (c !== undefined) return { kind: "byte", code: "8'd" + c };
    if (member(n) === "_callContext") return { kind: "byte", code: "contextByte" };
    if (
      n.type === "UpdateExpression" &&
      n.prefix &&
      n.operator === "--" &&
      reg(n.argument) === 21
    ) {
      decrements++;
      lines.push("followingGap=followingGap-8'd1;");
      return { kind: "byte", code: "followingGap" };
    }
    if (n.type === "BinaryExpression" && n.operator === "===") {
      const a = expression(n.left),
        b = expression(n.right);
      if (a.kind !== b.kind) throw Error("Mixed gap equality");
      return { kind: "bool", code: "(" + a.code + "==" + b.code + ")" };
    }
    throw Error("Unsupported gap expression " + n.type);
  }
  function statement(n) {
    if (n.type === "BlockStatement") {
      n.body.forEach(statement);
      return;
    }
    if (n.type === "BreakStatement" && !n.label) {
      breaks++;
      lines.push("active=1'b0;");
      return;
    }
    if (n.type === "IfStatement") {
      lines.push("if(active)begin");
      const t = expression(n.test);
      lines.push("if(" + t.code + ")begin");
      statement(n.consequent);
      lines.push("end");
      if (n.alternate) {
        lines.push("else begin");
        statement(n.alternate);
        lines.push("end");
      }
      lines.push("end");
      return;
    }
    if (n.type === "ThrowStatement") {
      const e = n.argument;
      if (
        e.type !== "NewExpression" ||
        e.callee.name !== "Error" ||
        e.arguments.length !== 1 ||
        e.arguments[0].type !== "TemplateLiteral" ||
        e.arguments[0].quasis.length !== 2 ||
        e.arguments[0].quasis[0].value.cooked !== "Unexpected call context " ||
        e.arguments[0].quasis[1].value.cooked !== "" ||
        e.arguments[0].expressions.length !== 1 ||
        member(e.arguments[0].expressions[0]) !== "_callContext"
      )
        throw Error("Changed gap trap");
      traps++;
      lines.push("trap=1'b1;active=1'b0;");
      return;
    }
    if (n.type === "ExpressionStatement" && n.expression.type === "CallExpression") {
      const e = n.expression,
        name = member(e.callee);
      calls.set(name, (calls.get(name) || 0) + 1);
      if (name === "_setState" && e.arguments.length === 1) {
        const x = expression(e.arguments[0]);
        if (x.kind !== "byte") throw Error("Nonbyte gap state");
        lines.push("stateWritten=1'b1;nextState=" + x.code + ";");
        return;
      }
      if (name === "_doWriteRun" && e.arguments.length === 2) {
        const a = expression(e.arguments[0]),
          b = expression(e.arguments[1]);
        if (a.kind !== "byte" || b.kind !== "byte") throw Error("Nonbyte write-run API");
        lines.push("writeRun=1'b1;writeContext=" + a.code + ";writeValue=" + b.code + ";");
        return;
      }
    }
    throw Error("Unsupported gap effect " + n.type);
  }
  selected[0].consequent.forEach(statement);
  if (
    decrements !== 1 ||
    breaks !== 2 ||
    traps !== 1 ||
    calls.get("_setState") !== 1 ||
    calls.get("_doWriteRun") !== 1 ||
    calls.size !== 2
  )
    throw Error("Changed gap effect inventory");
  return (
    "module FdcGapSource(input[7:0]gapByte,contextByte,output reg[7:0]followingGap,nextState,writeContext,writeValue,output reg stateWritten,writeRun,trap);reg active;always @* begin followingGap=gapByte;nextState=0;writeContext=0;writeValue=0;stateWritten=0;writeRun=0;trap=0;active=1;\n" +
    lines.join("\n") +
    "\nend endmodule\nmodule FdcGapWriteSource(input[7:0]value,output[7:0]followingGap);assign followingGap=value;endmodule\n"
  );
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  fs.mkdirSync(directory, { recursive: true });
  const source = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  fs.writeFileSync(directory + "/source.v", lowerGap(source));
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const fdc = Object.create(IntelFdc.prototype);
  fdc._regs = new Uint8Array(32);
  let state = 0,
    write = 0,
    ctx = 0,
    value = 0;
  fdc._setState = (x) => {
    state = x;
  };
  fdc._doWriteRun = (c, v) => {
    write = 1;
    ctx = c;
    value = v;
  };
  const bytes = Buffer.alloc(65536 * 7);
  let p = 0;
  for (let gap = 0; gap < 256; gap++)
    for (let context = 0; context < 256; context++) {
      fdc._state = 11;
      fdc._regs[21] = gap;
      fdc._callContext = context;
      state = write = ctx = value = 0;
      let trap = 0;
      try {
        IntelFdc.prototype._byteCallbackReading.call(fdc, 0, 255);
      } catch (e) {
        if (!(e instanceof Error) || e.message !== `Unexpected call context ${context}`) throw e;
        trap = 1;
      }
      for (const x of [fdc._regs[21], state ? 1 : 0, state, write, ctx, value, trap])
        bytes[p++] = x;
    }
  fs.writeFileSync(directory + "/original.bin", bytes);
  const writes = Buffer.alloc(256);
  for (let v = 0; v < 256; v++) {
    IntelFdc.prototype._writeRegister.call(fdc, 21, v);
    writes[v] = fdc._regs[21];
  }
  fs.writeFileSync(directory + "/writes.bin", writes);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({ passed: true, gap_cases: 65536, write_cases: 256, registers: 32 }) + "\n",
  );
}
