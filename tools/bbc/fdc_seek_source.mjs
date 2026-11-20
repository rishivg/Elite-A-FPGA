// Lower the original seek-target arithmetic; reject unsupported source changes.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";

export function lowerSeek(source) {
  const ast = parsers.babel.parse(source);
  const constants = new Map();
  function constant(node) {
    if (node.type === "NumericLiteral") return node.value;
    if (node.type === "BinaryExpression" && node.operator === "|")
      return constant(node.left) | constant(node.right);
    if (node.type === "BinaryExpression" && node.operator === "<<")
      return constant(node.left) << constant(node.right);
    if (
      node.type === "MemberExpression" &&
      !node.computed &&
      constants.has(node.object.name + "." + node.property.name)
    )
      return constants.get(node.object.name + "." + node.property.name);
    throw Error("Unsupported original enum expression: " + node.type);
  }
  for (const declaration of ast.program.body) {
    if (declaration.type !== "VariableDeclaration") continue;
    for (const variable of declaration.declarations) {
      if (!["Registers", "Call", "DriveOut"].includes(variable.id.name)) continue;
      const init = variable.init;
      if (
        init.type !== "CallExpression" ||
        init.callee.object?.name !== "Object" ||
        init.callee.property?.name !== "freeze" ||
        init.arguments.length !== 1 ||
        init.arguments[0].type !== "ObjectExpression"
      )
        throw Error("Changed original enum constructor");
      for (const property of init.arguments[0].properties)
        constants.set(variable.id.name + "." + property.key.name, constant(property.value));
    }
  }
  if (
    constants.get("Registers.internalParamCount") !== 1 ||
    constants.get("Registers.internalSeekRetryCount") !== 1
  )
    throw Error("Original parameter and retry counters no longer alias R1");
  const declarations = ast.program.body.filter(
    (n) => n.type === "ExportNamedDeclaration" && n.declaration?.id?.name === "IntelFdc",
  );
  if (declarations.length !== 1) throw Error("Expected one original IntelFdc class");
  const methods = declarations[0].declaration.body.body.filter((n) => n.key.name === "_doSeek");
  if (
    methods.length !== 1 ||
    methods[0].params.length !== 1 ||
    methods[0].params[0].name !== "callContext"
  )
    throw Error("Changed original seek method");
  const method = methods[0];
  const member = (node) =>
    node?.type === "MemberExpression" && !node.computed && node.object.type === "ThisExpression"
      ? node.property.name
      : null;
  const register = (node) =>
    node?.type === "MemberExpression" && node.computed && member(node.object) === "_regs";
  const end = method.body.body.findIndex(
    (n) =>
      n.type === "ExpressionStatement" &&
      n.expression.type === "AssignmentExpression" &&
      register(n.expression.left) &&
      n.expression.left.property.type === "MemberExpression" &&
      n.expression.left.property.property.name === "internalSeekTarget_1",
  );
  if (end < 0) throw Error("Missing original first seek-target store");
  const locals = new Set();
  const reads = new Set();
  const bounds = new Map();
  function interval(node) {
    if (node.type === "NumericLiteral") return [node.value, node.value];
    if (node.type === "Identifier" && node.name === "callContext") return [0, 0xffffffff];
    if (node.type === "Identifier" && bounds.has(node.name)) return bounds.get(node.name);
    if (member(node) === "_driveOut") return [0, 255];
    if (register(node)) {
      const address = interval(node.property);
      if (address[0] < 0 || address[1] > 31)
        throw Error("Original register address can leave the byte bank");
      return [0, 255];
    }
    if (
      node.type === "MemberExpression" &&
      constants.has(node.object.name + "." + node.property.name)
    ) {
      const value = constants.get(node.object.name + "." + node.property.name);
      return [value, value];
    }
    if (node.type === "ConditionalExpression") {
      const left = interval(node.consequent),
        right = interval(node.alternate);
      return [Math.min(left[0], right[0]), Math.max(left[1], right[1])];
    }
    if (node.type === "BinaryExpression") {
      const left = interval(node.left),
        right = interval(node.right);
      if ([">", "<=", "!=="].includes(node.operator)) return [0, 1];
      if (node.operator === "+") {
        if (left[0] < 0 || right[0] < 0 || left[1] + right[1] > 0x7fffffff)
          throw Error("Original Number addition can exceed the exact unsigned lowering domain");
        return [left[0] + right[0], left[1] + right[1]];
      }
      if (
        node.operator === "&" &&
        left[0] >= 0 &&
        right[0] >= 0 &&
        left[1] <= 0x7fffffff &&
        right[1] <= 0x7fffffff
      )
        return [0, Math.min(left[1], right[1])];
    }
    throw Error("Unsupported original arithmetic bounds: " + node.type + "/" + node.operator);
  }
  function expression(node) {
    if (node.type === "NumericLiteral") {
      if (!Number.isInteger(node.value) || node.value < 0 || node.value > 0x7fffffff)
        throw Error("Unbounded original arithmetic literal");
      return "32'd" + node.value;
    }
    if (node.type === "Identifier" && (locals.has(node.name) || node.name === "callContext"))
      return node.name;
    if (member(node) === "_driveOut") {
      reads.add("driveOut");
      return "{24'd0,driveOut}";
    }
    if (register(node)) {
      reads.add("registers");
      return "sourceReg(" + expression(node.property) + ")";
    }
    if (
      node.type === "MemberExpression" &&
      !node.computed &&
      constants.has(node.object.name + "." + node.property.name)
    )
      return "32'd" + constants.get(node.object.name + "." + node.property.name);
    if (node.type === "BinaryExpression" && ["+", "&", ">", "<=", "!=="].includes(node.operator)) {
      interval(node);
      return (
        "(" +
        expression(node.left) +
        " " +
        (node.operator === "!==" ? "!=" : node.operator) +
        " " +
        expression(node.right) +
        ")"
      );
    }
    if (node.type === "ConditionalExpression")
      return (
        "(" +
        "(" +
        expression(node.test) +
        " != 0) ? " +
        expression(node.consequent) +
        " : " +
        expression(node.alternate) +
        ")"
      );
    throw Error("Unsupported original seek expression: " + node.type + "/" + node.operator);
  }
  function statement(node) {
    if (node.type === "BlockStatement") return node.body.map(statement).join("\n");
    if (node.type === "VariableDeclaration")
      return node.declarations
        .map((n) => {
          if (n.id.type !== "Identifier" || locals.has(n.id.name))
            throw Error("Changed original seek local");
          const rhs = expression(n.init);
          bounds.set(n.id.name, interval(n.init));
          locals.add(n.id.name);
          return n.id.name + "=" + rhs + ";";
        })
        .join("\n");
    if (
      node.type === "ExpressionStatement" &&
      node.expression.type === "AssignmentExpression" &&
      node.expression.operator === "="
    ) {
      const n = node.expression;
      if (
        member(n.left) === "_callContext" &&
        n.right.type === "Identifier" &&
        n.right.name === "callContext"
      )
        return "// Call-context storage does not feed the target arithmetic.";
      if (n.left.type !== "Identifier" || !locals.has(n.left.name))
        throw Error("Unmodelled original seek prefix side effect");
      const rhs = expression(n.right),
        old = bounds.get(n.left.name),
        next = interval(n.right);
      bounds.set(n.left.name, [Math.min(old[0], next[0]), Math.max(old[1], next[1])]);
      return n.left.name + "=" + rhs + ";";
    }
    if (node.type === "IfStatement")
      return (
        "if((" +
        expression(node.test) +
        ") != 0)begin\n" +
        statement(node.consequent) +
        "\nend" +
        (node.alternate ? " else begin\n" + statement(node.alternate) + "\nend" : "")
      );
    throw Error("Unsupported original seek statement: " + node.type);
  }
  const body = method.body.body.slice(0, end).map(statement).join("\n");
  if (
    [...locals].join(",") !== "newTrack,trackRegOffset" ||
    [...reads].sort().join(",") !== "driveOut,registers"
  )
    throw Error("Changed original target dependency inventory");
  const prefix = source.slice(method.body.body[0].start, method.body.body[end].start);
  const verilog = `module FdcSeekSource(input[255:0]registers,input[7:0]driveOut,input[31:0]callContext,output[7:0]target,output[31:0]unclamped);
function[31:0]sourceReg(input[31:0]address);begin
 sourceReg={24'd0,registers[(address*8)+:8]};
end endfunction
${[...locals].map((n) => "reg[31:0]" + n + ";").join("\n")}
always @* begin
${body}
end
assign target=newTrack[7:0];assign unclamped=newTrack;
endmodule
`;
  return { verilog, prefix, locals: [...locals], dependencies: [...reads].sort() };
}

async function main() {
  const [root, directory] = process.argv.slice(2);
  const source = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  const projection = lowerSeek(source);
  fs.writeFileSync(directory + "/seek-source.v", projection.verilog);
  fs.writeFileSync(directory + "/seek-prefix.js", projection.prefix);
  fs.writeFileSync(
    directory + "/seek-projection.json",
    JSON.stringify({
      passed: true,
      locals: projection.locals,
      dependencies: projection.dependencies,
      full_byte_retry: true,
    }) + "\n",
  );
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const { Scheduler } = await import(pathToFileURL(root + "/src/scheduler.js"));
  const fdc = new IntelFdc({ setNmi() {}, nmiAsserted: () => false }, new Scheduler());
  fdc._log = () => {};
  // Retain the complete original arithmetic prefix. Observe the first target
  // assignment without executing drive motion or its independent timers.
  const registers = new Uint8Array(32);
  fdc._regs = new Proxy(registers, {
    set(array, key, value) {
      if (+key === 0x0b) throw { target: value };
      array[key] = value;
      return true;
    },
  });
  const rows = [];
  for (let requested = 0; requested < 256; requested++)
    for (let retry = 0; retry < 256; retry++)
      for (const bank of [0, 1]) {
        const sum = requested + retry;
        for (const [bad0, bad1] of [
          [0, 255],
          [255, 0],
          [requested, (sum + 1) & 255],
          [sum & 255, 255],
        ]) {
          registers[7] = requested;
          registers[1] = retry;
          registers[bank ? 0x18 : 0x10] = bad0;
          registers[bank ? 0x19 : 0x11] = bad1;
          fdc._driveOut = bank ? 0x80 : 0x40;
          let target;
          try {
            fdc._doSeek(1);
            throw Error("Missing original target assignment");
          } catch (error) {
            if (!Object.hasOwn(error, "target")) throw error;
            target = error.target;
          }
          if (!Number.isInteger(target) || target < 0 || target > 255)
            throw Error("Unbounded original target");
          rows.push([requested, retry, bank, bad0, bad1, target].join(" "));
        }
      }
  fs.writeFileSync(directory + "/seek-fixtures.tsv", rows.join("\n") + "\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
