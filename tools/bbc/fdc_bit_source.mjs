// Guarded lowering of the original bitwise-only, bounded pure methods.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";
export function lowerPure(source, className, methodName, moduleName, widths, inputMasks = []) {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(moduleName)) throw Error("Invalid module name");
  if (
    inputMasks.length > widths.length ||
    inputMasks.some((m) => !Number.isInteger(m) || m < 0 || m > 0xffffffff)
  )
    throw Error("Invalid pure input frame");
  const ast = parsers.babel.parse(source);
  const declarations = ast.program.body
    .map((n) => (n.type === "ExportNamedDeclaration" ? n.declaration : n))
    .filter((n) => n?.type === "ClassDeclaration" && n.id.name === className);
  if (declarations.length !== 1) throw Error("Missing unique original class");
  const methods = declarations[0].body.body.filter((n) => n.key?.name === methodName);
  if (methods.length !== 1) throw Error("Missing unique original pure method");
  const method = methods[0];
  if (
    !method.static ||
    method.kind !== "method" ||
    method.computed ||
    method.async ||
    method.generator ||
    method.params.length !== widths.length ||
    method.params.some((p) => p.type !== "Identifier")
  )
    throw Error("Changed pure method signature");
  const root = method.body.body;
  if (root.at(-1)?.type !== "ReturnStatement") throw Error("Expected one final pure return");
  let env = new Map(),
    serial = 0;
  const locals = [],
    lines = [];
  const allocate = (name, mutable) => {
    const id = "local" + serial++;
    locals.push(id);
    env.set(name, { id, mutable });
    return id;
  };
  const literal = (value) => {
    if (!Number.isInteger(value) || value < 0 || value > 0xffffffff)
      throw Error("Number outside bitwise literal domain");
    return "32'h" + value.toString(16);
  };
  function expr(n, allowedMask = null) {
    if (n.type === "NumericLiteral") return literal(n.value);
    if (n.type === "BooleanLiteral") return literal(+n.value);
    if (n.type === "Identifier") {
      const binding = env.get(n.name);
      if (!binding) throw Error("Unbound pure local " + n.name);
      if (
        binding.inputMask !== undefined &&
        (allowedMask === null || (allowedMask & ~binding.inputMask) !== 0)
      )
        throw Error("Original input escapes low-bit frame");
      return binding.id;
    }
    if (n.type === "BinaryExpression") {
      if (!["&", "|", "^", "<<", ">>>"].includes(n.operator))
        throw Error("Non-bitwise Number arithmetic");
      const left = expr(
          n.left,
          n.operator === "&" && n.right.type === "NumericLiteral" ? n.right.value : null,
        ),
        right = expr(
          n.right,
          n.operator === "&" && n.left.type === "NumericLiteral" ? n.left.value : null,
        );
      if (n.operator === "<<") return "(" + left + " << (" + right + " & 32'd31))";
      if (n.operator === ">>>") return "($unsigned(" + left + ") >> (" + right + " & 32'd31))";
      return "(" + left + " " + n.operator + " " + right + ")";
    }
    if (n.type === "UnaryExpression" && ["~", "!"].includes(n.operator))
      return n.operator === "!" ? "(" + expr(n.argument) + " == 0)" : "(~" + expr(n.argument) + ")";
    if (n.type === "ConditionalExpression")
      return (
        "((" + expr(n.test) + " != 0) ? " + expr(n.consequent) + " : " + expr(n.alternate) + ")"
      );
    throw Error("Unsupported pure expression: " + n.type);
  }
  function body(n) {
    if (n.type === "BlockStatement") {
      const previous = new Map(env);
      for (const s of n.body) statement(s);
      env = previous;
    } else statement(n);
  }
  function statement(n) {
    if (n.type === "VariableDeclaration") {
      if (!["let", "const"].includes(n.kind)) throw Error("Unsupported pure declaration scope");
      for (const v of n.declarations) {
        if (v.id.type !== "Identifier" || !v.init) throw Error("Unsupported pure declaration");
        const value = expr(v.init),
          id = allocate(v.id.name, n.kind !== "const");
        lines.push(id + " = " + value + ";");
      }
      return;
    }
    if (n.type === "ExpressionStatement" && n.expression.type === "AssignmentExpression") {
      const a = n.expression;
      if (a.left.type !== "Identifier") throw Error("Pure write escapes local state");
      const binding = env.get(a.left.name);
      if (!binding?.mutable) throw Error("Invalid pure local write");
      let value;
      if (a.operator === "=") value = expr(a.right);
      else if (["&=", "|=", "^=", "<<=", ">>>="].includes(a.operator))
        value = expr({
          type: "BinaryExpression",
          operator: a.operator.slice(0, -1),
          left: a.left,
          right: a.right,
        });
      else throw Error("Unsupported pure update");
      lines.push(binding.id + " = " + value + ";");
      return;
    }
    if (n.type === "IfStatement") {
      lines.push("if (" + expr(n.test) + " != 0) begin");
      body(n.consequent);
      lines.push("end");
      if (n.alternate) {
        lines.push("else begin");
        body(n.alternate);
        lines.push("end");
      }
      return;
    }
    if (n.type === "ForStatement") {
      const d = n.init;
      if (d?.type !== "VariableDeclaration" || d.kind !== "let" || d.declarations.length !== 1)
        throw Error("Unbounded pure loop");
      const v = d.declarations[0],
        test = n.test,
        update = n.update;
      if (
        v.id.type !== "Identifier" ||
        v.init?.type !== "NumericLiteral" ||
        v.init.value !== 0 ||
        test?.type !== "BinaryExpression" ||
        test.operator !== "<" ||
        test.left.type !== "Identifier" ||
        test.left.name !== v.id.name ||
        test.right.type !== "NumericLiteral" ||
        !Number.isInteger(test.right.value) ||
        test.right.value < 0 ||
        test.right.value > 32 ||
        update?.type !== "UpdateExpression" ||
        update.operator !== "++" ||
        update.argument.type !== "Identifier" ||
        update.argument.name !== v.id.name
      )
        throw Error("Unsupported bounded pure loop");
      const previous = new Map(env);
      for (let i = 0; i < test.right.value; i++) {
        env = new Map(previous);
        env.set(v.id.name, { id: literal(i), mutable: false });
        body(n.body);
      }
      env = previous;
      return;
    }
    throw Error("Unsupported pure statement: " + n.type);
  }
  method.params.forEach((p, i) => {
    if (!Number.isInteger(widths[i]) || widths[i] < 1 || widths[i] > 32)
      throw Error("Invalid pure input width");
    const id = allocate(p.name, true);
    if (inputMasks[i] !== undefined) env.get(p.name).inputMask = inputMasks[i];
    lines.push(id + " = arg" + i + ";");
  });
  for (const n of root.slice(0, -1)) statement(n);
  lines.push("value = " + expr(root.at(-1).argument) + ";");
  const ports = widths
    .map((w, i) => "input[" + (w - 1) + ":0]arg" + i)
    .concat("output reg[31:0]value")
    .join(",");
  return (
    "module " +
    moduleName +
    "(" +
    ports +
    ");\n" +
    locals.map((id) => "reg[31:0] " + id + ";").join("\n") +
    "\nalways @(*) begin\n" +
    locals.map((id) => id + " = 0;").join("\n") +
    "\n" +
    lines.join("\n") +
    "\nend\nendmodule\n"
  );
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  const disc = fs.readFileSync(root + "/src/disc.js", "utf8"),
    fdc = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  const rtl =
    lowerPure(disc, "IbmDiscFormat", "crcAddByte", "FdcCrcSource", [16, 8]) +
    lowerPure(disc, "IbmDiscFormat", "fmTo2usPulses", "FdcFmSource", [8, 8]) +
    lowerPure(fdc, "IntelFdc", "extractBits", "FdcExtractSource", [32], [0xffff]);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(directory + "/source.v", rtl);
  const { IbmDiscFormat } = await import(pathToFileURL(root + "/src/disc.js"));
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const crc = Buffer.alloc(65536 * 256 * 2);
  let position = 0;
  for (let initial = 0; initial < 65536; initial++)
    for (let byte = 0; byte < 256; byte++) {
      const value = IbmDiscFormat.crcAddByte(initial, byte);
      if (!Number.isInteger(value) || value < 0 || value > 65535)
        throw Error("CRC leaves declared output domain");
      crc.writeUInt16LE(value, position);
      position += 2;
    }
  fs.writeFileSync(directory + "/crc.bin", crc);
  const fm = Buffer.alloc(65536 * 4),
    extract = Buffer.alloc(65536);
  for (let word = 0; word < 65536; word++) {
    fm.writeUInt32LE(IbmDiscFormat.fmTo2usPulses(word >>> 8, word & 255), word * 4);
    const value = IntelFdc.extractBits(word);
    if (!Number.isInteger(value) || value < 0 || value > 255)
      throw Error("Extraction leaves byte domain");
    extract[word] = value;
  }
  fs.writeFileSync(directory + "/fm.bin", fm);
  fs.writeFileSync(directory + "/extract.bin", extract);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({
      passed: true,
      crc_cases: 65536 * 256,
      fm_cases: 65536,
      extract_cases: 65536,
      domain:
        "Pure bounded bitwise methods, CRC input16/8, FM8/8, extraction32; Number additions and nonlocal effects rejected.",
    }) + "\n",
  );
}
