// Original parameter acceptance, with register I/O and dispatch boundaries explicit.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";

export function lowerParameter(source) {
  const ast = parsers.babel.parse(source),
    constants = new Map();
  for (const declaration of ast.program.body) {
    if (declaration.type !== "VariableDeclaration") continue;
    for (const variable of declaration.declarations) {
      if (!["Registers", "ParamAccept"].includes(variable.id.name)) continue;
      const init = variable.init;
      if (
        init?.type !== "CallExpression" ||
        init.callee.object?.name !== "Object" ||
        init.callee.property?.name !== "freeze" ||
        init.arguments.length !== 1 ||
        init.arguments[0].type !== "ObjectExpression"
      )
        throw Error("Changed parameter enum");
      for (const property of init.arguments[0].properties) {
        if (property.value.type !== "NumericLiteral")
          throw Error("Unsupported parameter enum expression");
        constants.set(variable.id.name + "." + property.key.name, property.value.value);
      }
    }
  }
  if (
    constants.get("Registers.internalPointer") !== 0 ||
    constants.get("Registers.internalParamCount") !== 1
  )
    throw Error("Changed parameter register aliases");
  const classes = ast.program.body.filter(
    (n) => n.type === "ExportNamedDeclaration" && n.declaration?.id?.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Expected original IntelFdc class");
  const methods = new Map(classes[0].declaration.body.body.map((n) => [n.key.name, n]));
  const size = methods.get("NumRegisters");
  if (
    !size?.static ||
    size.kind !== "get" ||
    size.body.body.length !== 1 ||
    size.body.body[0].type !== "ReturnStatement" ||
    size.body.body[0].argument.value !== 32
  )
    throw Error("Changed original register-bank size");
  const method = methods.get("_paramWritten");
  if (!method || method.params.length !== 1 || method.params[0].name !== "param")
    throw Error("Changed parameter method");
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
  const register = (n) =>
    n?.type === "MemberExpression" && n.computed && member(n.object) === "_regs";
  function walk(node, visit) {
    if (!node || typeof node !== "object") return;
    if (node.type) visit(node);
    for (const [key, value] of Object.entries(node))
      if (!["loc", "start", "end", "comments"].includes(key)) {
        if (Array.isArray(value)) value.forEach((child) => walk(child, visit));
        else if (value && typeof value === "object") walk(value, visit);
      }
  }
  const framed = new Set();
  function counterFrame(name) {
    if (framed.has(name)) return;
    framed.add(name);
    const method = methods.get(name);
    if (!method) throw Error("Unknown counter-frame helper: " + name);
    walk(method.body, (node) => {
      const target =
        node.type === "AssignmentExpression"
          ? node.left
          : node.type === "UpdateExpression"
            ? node.argument
            : null;
      if (register(target)) {
        const property = target.property;
        const a =
          property.type === "NumericLiteral"
            ? property.value
            : constants.get(property.object?.name + "." + property.property?.name);
        if (!Number.isInteger(a) || a === 0 || a === 1)
          throw Error("Helper changes the parameter counter frame: " + name);
      }
      if (node.type === "CallExpression") {
        const callee = member(node.callee);
        if (callee && !["_log", "_logCommand"].includes(callee)) counterFrame(callee);
      }
    });
  }
  for (const name of ["_resultConsumed", "_finishSimpleCommand", "_setDriveOut"])
    counterFrame(name);
  const declarations = [];
  let nextTemporary = 0;
  function temporary() {
    const n = "temp" + nextTemporary++;
    declarations.push("reg[31:0]" + n + ";");
    return n;
  }
  function constant(n) {
    if (n.type === "NumericLiteral") return n.value;
    if (
      n.type === "MemberExpression" &&
      !n.computed &&
      constants.has(n.object.name + "." + n.property.name)
    )
      return constants.get(n.object.name + "." + n.property.name);
    throw Error("Unsupported parameter constant");
  }
  function address(n) {
    const a = constant(n);
    if (a < 0 || a > 31) throw Error("Out-of-range parameter register");
    return a;
  }
  function expression(n, lines) {
    if (
      n.type === "NumericLiteral" ||
      (n.type === "MemberExpression" && constants.has(n.object.name + "." + n.property.name))
    )
      return "32'd" + constant(n);
    if (n.type === "Identifier" && n.name === "param") return "{24'd0,param}";
    if (member(n) === "_paramCallback") return "{30'd0,callback}";
    if (register(n)) return "{24'd0,following[" + address(n.property) * 8 + "+:8]}";
    if (
      n.type === "UpdateExpression" &&
      register(n.argument) &&
      ["++", "--"].includes(n.operator)
    ) {
      const a = address(n.argument.property),
        old = temporary(),
        updated = temporary();
      lines.push(
        old + "={24'd0,following[" + a * 8 + "+:8]};",
        updated + "=" + old + (n.operator === "++" ? "+" : "-") + "32'd1;",
        "following[" + a * 8 + "+:8]=" + updated + "[7:0];",
      );
      return n.prefix ? updated : old;
    }
    if (n.type === "BinaryExpression" && n.operator === "===")
      return "(" + expression(n.left, lines) + "==" + expression(n.right, lines) + ")";
    throw Error("Unsupported parameter expression: " + n.type + "/" + n.operator);
  }
  function statement(n) {
    const lines = [];
    if (n.type === "BlockStatement") return n.body.flatMap(statement);
    if (n.type === "BreakStatement") return [];
    if (n.type === "ThrowStatement") return ["trap=1;"];
    if (n.type === "IfStatement") {
      const test = expression(n.test, lines);
      lines.push("if(" + test + ")begin", ...statement(n.consequent), "end");
      if (n.alternate) lines.push("else begin", ...statement(n.alternate), "end");
      return lines;
    }
    if (n.type === "SwitchStatement") {
      const selector = expression(n.discriminant, lines);
      lines.push("case(" + selector + ")");
      for (const c of n.cases)
        lines.push(
          (c.test ? "32'd" + constant(c.test) : "default") + ":begin",
          ...c.consequent.flatMap(statement),
          "end",
        );
      lines.push("endcase");
      return lines;
    }
    if (n.type !== "ExpressionStatement") throw Error("Unsupported parameter statement: " + n.type);
    const e = n.expression;
    if (e.type === "AssignmentExpression" && e.operator === "=" && register(e.left)) {
      const rhs = expression(e.right, lines);
      lines.push("following[" + address(e.left.property) * 8 + "+:8]=" + rhs + ";");
      return lines;
    }
    if (e.type === "UpdateExpression") {
      expression(e, lines);
      return lines;
    }
    if (e.type !== "CallExpression") throw Error("Unsupported parameter side effect");
    const call = member(e.callee);
    if (call === "_resultConsumed" && e.arguments.length === 0) return [];
    if (call === "_logCommand") {
      if (
        e.arguments.length !== 1 ||
        e.arguments[0].type !== "TemplateLiteral" ||
        e.arguments[0].expressions.some(
          (x) =>
            x.type !== "CallExpression" ||
            x.callee.type !== "Identifier" ||
            x.callee.name !== "hexbyte" ||
            x.arguments.length !== 1 ||
            x.arguments[0].type !== "Identifier" ||
            x.arguments[0].name !== "param",
        )
      )
        throw Error("Parameter logging gained a side effect");
      return [];
    }
    if (call === "_startCommand" && e.arguments.length === 0) return ["started=1;"];
    if (call === "_finishSimpleCommand" && e.arguments.length === 0) return ["finished=1;"];
    if (call === "_writeRegister" && e.arguments.length === 2) {
      const a = temporary(),
        value = temporary();
      lines.push(
        a + "=" + expression(e.arguments[0], lines) + ";",
        value + "=" + expression(e.arguments[1], lines) + ";",
      );
      lines.push(
        "if((" + a + "&32'd63)<32)following[((" + a + "&32'd31)*8)+:8]=" + value + "[7:0];",
      );
      return lines;
    }
    throw Error("Unsupported parameter helper: " + call);
  }
  const lines = statement(method.body);
  const write = methods.get("_writeRegister");
  if (!write || write.body.body.length < 2) throw Error("Missing original register-write prefix");
  const mask = write.body.body[0],
    branch = write.body.body[1];
  if (
    mask.type !== "ExpressionStatement" ||
    mask.expression.type !== "AssignmentExpression" ||
    mask.expression.operator !== "&=" ||
    mask.expression.left.name !== "reg" ||
    mask.expression.right.value !== 63 ||
    branch.type !== "IfStatement" ||
    branch.test.type !== "BinaryExpression" ||
    branch.test.operator !== "<" ||
    branch.test.left.name !== "reg" ||
    branch.test.right.object?.name !== "IntelFdc" ||
    branch.test.right.property?.name !== "NumRegisters"
  )
    throw Error("Changed register-write RAM/MMIO boundary");
  const ram = branch.consequent;
  if (
    branch.alternate ||
    ram.type !== "BlockStatement" ||
    ram.body.length !== 2 ||
    ram.body[1].type !== "ReturnStatement" ||
    ram.body[0].type !== "ExpressionStatement"
  )
    throw Error("Changed original RAM-write body");
  const store = ram.body[0].expression;
  if (
    store.type !== "AssignmentExpression" ||
    store.operator !== "=" ||
    !register(store.left) ||
    store.left.property.name !== "reg" ||
    store.right.name !== "val"
  )
    throw Error("Changed typed-byte RAM assignment");
  for (const statement of write.body.body.slice(2))
    walk(statement, (node) => {
      const target =
        node.type === "AssignmentExpression"
          ? node.left
          : node.type === "UpdateExpression"
            ? node.argument
            : null;
      if (register(target)) throw Error("MMIO helper gained a controller-RAM write");
      if (node.type === "CallExpression") {
        const callee = member(node.callee);
        if (callee && !["_log", "_logCommand"].includes(callee)) counterFrame(callee);
      }
    });
  const writePrefix = source.slice(write.body.body[0].start, write.body.body[1].end);
  // The projection stops at dispatch/finish and omits MMIO side effects, which
  // do not write R0/R1. These are exposed interfaces, not hidden assumptions.
  const text = `module FdcParameterSource(input[255:0]registers,input[7:0]param,input[1:0]callback,output reg[255:0]following,output reg started,finished,trap);
${declarations.join("\n")}
always @* begin
 following=registers;started=0;finished=0;trap=0;
${declarations.map((n) => n.match(/temp[0-9]+/)[0] + "=0;").join("\n")}
${lines.join("\n")}
end
endmodule
`;
  return {
    verilog: text,
    writePrefix,
    method: source.slice(method.start, method.end),
    temporaries: nextTemporary,
  };
}

async function main() {
  const [root, directory] = process.argv.slice(2),
    source = fs.readFileSync(root + "/src/intel-fdc.js", "utf8"),
    projection = lowerParameter(source);
  fs.writeFileSync(directory + "/parameter-source.v", projection.verilog);
  fs.writeFileSync(
    directory + "/parameter-projection.json",
    JSON.stringify({
      passed: true,
      temporaries: projection.temporaries,
      interfaces: ["register-MMIO", "start-command", "finish-simple-command", "external-status"],
    }) + "\n",
  );
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
  const fdc = new IntelFdc({ setNmi() {}, nmiAsserted: () => false }, new Scheduler());
  fdc._log = () => {};
  fdc._logCommand = () => {};
  fdc._resultConsumed = () => {};
  const write = Function("IntelFdc", "reg", "val", projection.writePrefix);
  fdc._writeRegister = function (reg, val) {
    return write.call(this, IntelFdc, reg, val);
  };
  let started, finished;
  fdc._startCommand = () => {
    started = 1;
  };
  fdc._finishSimpleCommand = () => {
    finished = 1;
  };
  const rows = [];
  for (const callback of [1, 2])
    for (let pointer = 0; pointer < 256; pointer++)
      for (let count = 0; count < 256; count++)
        for (const data of [0, 1, 2, 255]) {
          fdc._regs.fill(0);
          fdc._regs[0] = pointer;
          fdc._regs[1] = count;
          fdc._paramCallback = callback;
          started = finished = 0;
          fdc._paramWritten(data);
          rows.push(
            [pointer, count, data, callback, fdc._regs[0], fdc._regs[1], started, finished].join(
              " ",
            ),
          );
        }
  fs.writeFileSync(directory + "/parameter-fixtures.tsv", rows.join("\n") + "\n");
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
