// Lower the original byte IRQ, eligibility and Uint8Array counter methods.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";

export function lowerByteControl(source) {
  const ast = parsers.babel.parse(source),
    constants = new Map();
  for (const declaration of ast.program.body) {
    if (declaration.type !== "VariableDeclaration") continue;
    for (const variable of declaration.declarations) {
      if (
        !["Registers", "DriveOut", "FdcMode", "Command", "StatusFlag", "Result"].includes(
          variable.id.name,
        )
      )
        continue;
      const init = variable.init;
      if (
        init?.type !== "CallExpression" ||
        init.callee.object?.name !== "Object" ||
        init.callee.property?.name !== "freeze" ||
        init.arguments.length !== 1 ||
        init.arguments[0].type !== "ObjectExpression"
      )
        throw Error("Changed byte-control constants");
      for (const property of init.arguments[0].properties) {
        if (
          property.type !== "ObjectProperty" ||
          property.computed ||
          property.value.type !== "NumericLiteral" ||
          !Number.isInteger(property.value.value) ||
          property.value.value < 0 ||
          property.value.value > 0xffffffff
        )
          throw Error("Unsupported byte-control constant");
        const key = variable.id.name + "." + property.key.name;
        if (constants.has(key)) throw Error("Duplicate byte-control constant");
        constants.set(key, property.value.value);
      }
    }
  }
  const expected = {
    "Registers.internalCountLsb": 19,
    "Registers.internalCountMsb": 20,
    "Registers.internalStatus": 23,
    "Registers.mode": 23,
    "Registers.internalCommand": 31,
  };
  for (const [name, value] of Object.entries(expected))
    if (constants.get(name) !== value) throw Error("Changed byte register alias: " + name);
  const classes = ast.program.body.filter(
    (n) => n.type === "ExportNamedDeclaration" && n.declaration?.id?.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Missing unique IntelFdc");
  const methods = classes[0].declaration.body.body;
  const get = (name, kind = "method") => {
    const selected = methods.filter((n) => n.key?.name === name);
    if (
      selected.length !== 1 ||
      selected[0].kind !== kind ||
      selected[0].static ||
      selected[0].computed ||
      selected[0].async ||
      selected[0].generator ||
      selected[0].params.length
    )
      throw Error("Changed original byte method: " + name);
    return selected[0];
  };
  const size = methods.filter((n) => n.key?.name === "NumRegisters");
  if (
    size.length !== 1 ||
    !size[0].static ||
    size[0].kind !== "get" ||
    size[0].body.body.length !== 1 ||
    size[0].body.body[0].type !== "ReturnStatement" ||
    size[0].body.body[0].argument?.value !== 32
  )
    throw Error("Changed Uint8 register-bank size");
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
  const register = (n) =>
    n?.type === "MemberExpression" && n.computed && member(n.object) === "_regs";
  const constructors = methods.filter((n) => n.kind === "constructor");
  const banks = [];
  function inspect(node) {
    if (!node || typeof node !== "object") return;
    if (node.type === "AssignmentExpression" && member(node.left) === "_regs") banks.push(node);
    for (const [key, value] of Object.entries(node))
      if (!["loc", "start", "end", "comments"].includes(key)) {
        if (Array.isArray(value)) value.forEach(inspect);
        else if (value && typeof value === "object") inspect(value);
      }
  }
  if (constructors.length !== 1) throw Error("Changed original constructor");
  inspect(constructors[0].body);
  const bank = banks[0]?.right;
  if (
    banks.length !== 1 ||
    banks[0].operator !== "=" ||
    bank.type !== "NewExpression" ||
    bank.callee.name !== "Uint8Array" ||
    bank.arguments.length !== 1 ||
    bank.arguments[0].type !== "MemberExpression" ||
    bank.arguments[0].computed ||
    bank.arguments[0].object.name !== "IntelFdc" ||
    bank.arguments[0].property.name !== "NumRegisters"
  )
    throw Error("Original register bank is no longer Uint8Array(32)");
  const slots = new Map([
    [19, "r_low"],
    [20, "r_high"],
    [23, "r_status"],
    [31, "r_command"],
  ]);
  const getters = new Map(
    ["_irqCallbacks", "internalStatus", "_internalCommand"].map((n) => [n, get(n, "get")]),
  );
  for (const m of getters.values())
    if (m.body.body.length !== 1 || m.body.body[0].type !== "ReturnStatement")
      throw Error("Impure byte getter");
  const counter = get("_decrementCounter"),
    stop = get("_stopIrqCallbacks"),
    loss = get("_checkDataLossOk");
  if (stop.body.body.length !== 1 || stop.body.body[0].type !== "ExpressionStatement")
    throw Error("Changed IRQ-stop boundary");

  function lower(method, name, role) {
    let serial = 0,
      env = new Map();
    const locals = [],
      lines = [],
      active = new Set(),
      reads = new Set();
    const allocate = () => {
      const n = "local" + serial++;
      locals.push(n);
      return n;
    };
    const number = (code) => ({ code, kind: "num" });
    const boolean = (code) => ({ code, kind: "bool" });
    const literal = (n) => {
      if (!Number.isInteger(n) || n < 0 || n > 0xffffffff)
        throw Error("Unsupported byte Number literal");
      return number("64'h" + n.toString(16));
    };
    function address(node) {
      if (!register(node) || node.property.type !== "MemberExpression" || node.property.computed)
        throw Error("Nonconstant byte register");
      const value = constants.get(node.property.object.name + "." + node.property.property.name);
      if (!slots.has(value)) throw Error("Byte method escapes declared registers");
      return value;
    }
    function readGetter(n) {
      if (active.has(n)) throw Error("Recursive byte getter");
      active.add(n);
      const result = expr(getters.get(n).body.body[0].argument, true);
      active.delete(n);
      return result;
    }
    function expr(n, pure = false) {
      if (n.type === "NumericLiteral") return literal(n.value);
      if (n.type === "BooleanLiteral") return boolean(n.value ? "64'd1" : "64'd0");
      if (n.type === "Identifier") {
        if (!env.has(n.name)) throw Error("Unbound byte local");
        return env.get(n.name);
      }
      if (register(n)) {
        const a = address(n);
        reads.add(a);
        if (role === "irq" && a !== 23) throw Error("IRQ getter reads another register");
        if (role === "counter" && ![19, 20, 23].includes(a))
          throw Error("Counter reads undeclared state");
        if (role === "loss" && ![23, 31].includes(a))
          throw Error("Eligibility reads undeclared state");
        return number("{56'd0," + slots.get(a) + "}");
      }
      const field = member(n);
      if (getters.has(field)) return readGetter(field);
      if (n.type === "MemberExpression" && !n.computed && n.object.type === "Identifier") {
        const key = n.object.name + "." + n.property.name;
        if (!constants.has(key)) throw Error("Unbound byte constant");
        return literal(constants.get(key));
      }
      if (
        n.type === "UpdateExpression" &&
        n.operator === "--" &&
        n.prefix &&
        register(n.argument)
      ) {
        if (pure || role !== "counter") throw Error("Unexpected byte mutation");
        const a = address(n.argument);
        if (![19, 20].includes(a)) throw Error("Counter decrement escapes its registers");
        const temporary = allocate();
        lines.push(
          temporary + " = {56'd0," + slots.get(a) + "} - 64'd1;",
          slots.get(a) + " = " + temporary + "[7:0];",
        );
        return number(temporary);
      }
      if (n.type === "UnaryExpression" && ["!", "~"].includes(n.operator)) {
        const v = expr(n.argument, pure);
        return n.operator === "!"
          ? boolean("(" + v.code + " == 0)")
          : number("jsInt(~" + v.code + ")");
      }
      if (n.type === "BinaryExpression") {
        const a = expr(n.left, pure),
          b = expr(n.right, pure);
        if (["===", "!=="].includes(n.operator)) {
          if (a.kind !== b.kind) return boolean(n.operator === "!==" ? "64'd1" : "64'd0");
          return boolean("(" + a.code + (n.operator === "===" ? " == " : " != ") + b.code + ")");
        }
        if (!["&", "|", "^", "<<", ">>>"].includes(n.operator))
          throw Error("Unmodelled byte Number arithmetic");
        if (n.operator === "<<")
          return number("jsInt(" + a.code + " << (" + b.code + " & 64'd31))");
        if (n.operator === ">>>")
          return number("jsUint(jsUint(" + a.code + ") >> (" + b.code + " & 64'd31))");
        return number("jsInt(" + a.code + " " + n.operator + " " + b.code + ")");
      }
      if (n.type === "LogicalExpression" && ["&&", "||"].includes(n.operator)) {
        const a = expr(n.left, true),
          b = expr(n.right, true);
        if (a.kind !== "bool" || b.kind !== "bool") throw Error("Nonboolean short circuit");
        return boolean("(" + a.code + " " + n.operator + " " + b.code + ")");
      }
      throw Error("Unsupported byte expression: " + n.type);
    }
    function statement(n) {
      lines.push("if (!returned) begin");
      if (n.type === "BlockStatement") {
        const previous = new Map(env);
        for (const v of n.body) statement(v);
        env = previous;
      } else if (n.type === "VariableDeclaration" && ["let", "const"].includes(n.kind)) {
        for (const d of n.declarations) {
          if (d.id.type !== "Identifier" || !d.init || env.has(d.id.name))
            throw Error("Changed byte local scope");
          const value = expr(d.init),
            local = allocate();
          lines.push(local + " = " + value.code + ";");
          env.set(d.id.name, { code: local, kind: value.kind, mutable: n.kind === "let" });
        }
      } else if (n.type === "IfStatement") {
        const test = expr(n.test);
        lines.push("if (" + test.code + " != 0) begin");
        statement(n.consequent);
        lines.push("end");
        if (n.alternate) {
          lines.push("else begin");
          statement(n.alternate);
          lines.push("end");
        }
      } else if (n.type === "ReturnStatement") {
        const value = expr(n.argument);
        if (value.kind !== "bool") throw Error("Byte method no longer returns boolean");
        lines.push("value = " + value.code + " != 0;", "returned = 1;");
      } else if (n.type === "ExpressionStatement") {
        const e = n.expression;
        if (e.type === "UpdateExpression") expr(e);
        else if (e.type === "AssignmentExpression") {
          const v = expr(e.right);
          if (e.left.type === "Identifier") {
            const target = env.get(e.left.name);
            if (e.operator !== "=" || !target?.mutable || target.kind !== v.kind)
              throw Error("Changed byte local assignment");
            lines.push(target.code + " = " + v.code + ";");
          } else {
            if (role !== "counter") throw Error("Eligibility/getter writes register state");
            const a = address(e.left),
              target = slots.get(a);
            if (![19, 20, 23].includes(a)) throw Error("Byte write escapes declared registers");
            if (e.operator === "=") lines.push(target + " = " + v.code + ";");
            else if (e.operator === "&=")
              lines.push(target + " = " + target + " & " + v.code + ";");
            else throw Error("Unsupported byte register assignment");
          }
        } else if (
          e.type === "CallExpression" &&
          member(e.callee) === "_stopIrqCallbacks" &&
          !e.arguments.length &&
          role === "counter"
        ) {
          for (const v of stop.body.body) statement(v);
        } else if (
          e.type === "CallExpression" &&
          member(e.callee) === "_commandAbort" &&
          !e.arguments.length &&
          role === "loss"
        )
          lines.push("aborted = 1;");
        else if (
          e.type === "CallExpression" &&
          member(e.callee) === "_finishCommand" &&
          e.arguments.length === 1 &&
          role === "loss"
        ) {
          const v = expr(e.arguments[0], true);
          if (v.kind !== "num") throw Error("Changed finish result");
          lines.push("finished = 1;", "finishResult = " + v.code + ";");
        } else throw Error("Unknown byte effect");
      } else throw Error("Unsupported byte statement: " + n.type);
      lines.push("end");
    }
    if (role === "irq") {
      const result = readGetter("_irqCallbacks");
      if (result.kind !== "bool") throw Error("Nonboolean IRQ getter");
      lines.push("value = " + result.code + " != 0;", "returned = 1;");
    } else for (const v of method.body.body) statement(v);
    return `module ${name}(input[7:0]low,high,status,command,output[7:0]nextLow,nextHigh,nextStatus,output reg value,aborted,finished,output reg[7:0]finishResult);
reg[7:0]r_low,r_high,r_status,r_command;reg returned;
${locals.map((n) => "reg[63:0]" + n + ";").join("\n")}
function[63:0]jsInt(input[63:0]v);begin jsInt={{32{v[31]}},v[31:0]};end endfunction
function[63:0]jsUint(input[63:0]v);begin jsUint={32'd0,v[31:0]};end endfunction
assign nextLow=r_low;assign nextHigh=r_high;assign nextStatus=r_status;
always @(*) begin
r_low=low;r_high=high;r_status=status;r_command=command;returned=0;value=0;aborted=0;finished=0;finishResult=0;
${locals.map((n) => n + "=0;").join("\n")}
${lines.join("\n")}
end
endmodule
`;
  }
  return (
    lower(getters.get("_irqCallbacks"), "FdcIrqSource", "irq") +
    lower(counter, "FdcCounterSource", "counter") +
    lower(loss, "FdcLossSource", "loss")
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2),
    source = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  const verilog = lowerByteControl(source);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(directory + "/source.v", verilog);
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const original = Object.create(IntelFdc.prototype);
  original._regs = new Uint8Array(32);
  let aborted = 0,
    finished = 0,
    result = 0;
  original._commandAbort = () => {
    aborted++;
  };
  original._finishCommand = (value) => {
    finished++;
    result = value;
  };
  const irq = Buffer.alloc(256),
    loss = Buffer.alloc(65536 * 2),
    counter = Buffer.alloc(16777216 * 4);
  for (let status = 0; status < 256; status++) {
    original._regs[23] = status;
    assertBoolean(original._irqCallbacks);
    irq[status] = +original._irqCallbacks;
    for (let command = 0; command < 256; command++) {
      original._regs[23] = status;
      original._regs[31] = command;
      aborted = 0;
      finished = 0;
      result = 0;
      const ok = original._checkDataLossOk();
      assertBoolean(ok);
      if (aborted > 1 || finished > 1 || !Number.isInteger(result) || result < 0 || result > 255)
        throw Error("Eligibility effect escaped boundary");
      loss.writeUInt16LE(
        +ok | (aborted << 1) | (finished << 2) | (result << 8),
        (status * 256 + command) * 2,
      );
    }
    for (let high = 0; high < 256; high++)
      for (let low = 0; low < 256; low++) {
        original._regs[19] = low;
        original._regs[20] = high;
        original._regs[23] = status;
        const done = original._decrementCounter();
        assertBoolean(done);
        counter.writeUInt32LE(
          (original._regs[19] |
            (original._regs[20] << 8) |
            (original._regs[23] << 16) |
            (+done << 24)) >>>
            0,
          ((status * 256 + high) * 256 + low) * 4,
        );
      }
  }
  fs.writeFileSync(directory + "/irq.bin", irq);
  fs.writeFileSync(directory + "/loss.bin", loss);
  fs.writeFileSync(directory + "/counter.bin", counter);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({ passed: true, irq_cases: 256, loss_cases: 65536, counter_cases: 16777216 }) +
      "\n",
  );
}
function assertBoolean(value) {
  if (typeof value !== "boolean") throw Error("Original byte result leaves boolean domain");
}
