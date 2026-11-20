// Lower the original sync/check-ID prefix, stopping at the byte-callback boundary.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";

export function lowerSync(source) {
  const ast = parsers.babel.parse(source);
  const stateDeclarations = ast.program.body.flatMap((n) =>
    n.type === "VariableDeclaration" ? n.declarations.filter((d) => d.id.name === "State") : [],
  );
  if (stateDeclarations.length !== 1) throw Error("Missing unique source states");
  const init = stateDeclarations[0].init;
  if (
    init?.type !== "CallExpression" ||
    init.callee.object?.name !== "Object" ||
    init.callee.property?.name !== "freeze" ||
    init.arguments.length !== 1 ||
    init.arguments[0].type !== "ObjectExpression"
  )
    throw Error("Changed source state enum");
  const states = new Map();
  for (const p of init.arguments[0].properties) {
    if (
      p.type !== "ObjectProperty" ||
      p.computed ||
      p.value.type !== "NumericLiteral" ||
      !Number.isInteger(p.value.value) ||
      p.value.value < 0 ||
      p.value.value > 255
    )
      throw Error("Unsupported source state constant");
    const name = p.key.name ?? p.key.value;
    if (states.has(name)) throw Error("Duplicate source state");
    states.set(name, p.value.value);
  }
  for (const name of ["syncingForId", "syncingForData", "checkIdMarker", "checkDataMarker"])
    if (!states.has(name)) throw Error("Missing source delimiter state");
  if (new Set(states.values()).size !== states.size) throw Error("Aliased source states");
  const classes = ast.program.body.filter(
    (n) => n.type === "ExportNamedDeclaration" && n.declaration?.id?.name === "IntelFdc",
  );
  if (classes.length !== 1) throw Error("Missing unique source controller");
  const get = (name) => {
    const selected = classes[0].declaration.body.body.filter((n) => n.key?.name === name);
    if (
      selected.length !== 1 ||
      selected[0].kind !== "method" ||
      selected[0].computed ||
      selected[0].async ||
      selected[0].generator ||
      selected[0].params.some((p) => p.type !== "Identifier")
    )
      throw Error("Changed source method: " + name);
    return selected[0];
  };
  const shift = get("_shiftDataBit"),
    setter = get("_setState"),
    extract = get("extractBits");
  if (
    shift.static ||
    setter.static ||
    !extract.static ||
    shift.params.length !== 1 ||
    setter.params.length !== 1 ||
    extract.params.length !== 1 ||
    shift.body.body.length !== 2 ||
    shift.body.body[0].type !== "VariableDeclaration" ||
    shift.body.body[1].type !== "SwitchStatement"
  )
    throw Error("Changed original bit-step boundary");
  const logger = get("_log");
  const logCall = logger.body.body[0]?.expression;
  if (
    logger.body.body.length !== 1 ||
    logCall?.type !== "CallExpression" ||
    logCall.callee.object?.name !== "console" ||
    logCall.callee.property?.name !== "log"
  )
    throw Error("Source logger acquired nonlocal effects");
  let serial = 0,
    env = new Map();
  const locals = [],
    lines = [];
  const fields = new Map([
    ["_state", "sourceState"],
    ["_stateCount", "sourceAge"],
    ["_shiftRegister", "sourceShift"],
    ["_numShifts", "sourceCount"],
  ]);
  const allocate = () => {
    const n = "local" + serial++;
    locals.push(n);
    return n;
  };
  const literal = (n) => {
    if (!Number.isSafeInteger(n) || n < 0 || n > 0xffffffff)
      throw Error("Unsupported source Number literal");
    return { code: "64'h" + n.toString(16), kind: "num" };
  };
  const member = (n) =>
    n?.type === "MemberExpression" && !n.computed && n.object.type === "ThisExpression"
      ? n.property.name
      : null;
  const truth = (n) => "(" + expression(n, true).code + " != 0)";
  function pureLog(n, allowed = new Set(env.keys())) {
    if (n.type === "TemplateLiteral") {
      n.expressions.forEach((e) => pureLog(e, allowed));
      return;
    }
    if (
      (n.type === "Identifier" && allowed.has(n.name)) ||
      n.type === "StringLiteral" ||
      n.type === "NumericLiteral" ||
      fields.has(member(n)) ||
      member(n) === "_logStateChanges"
    )
      return;
    throw Error("Source logging argument acquired effects");
  }
  logCall.arguments.forEach((n) => pureLog(n, new Set(logger.params.map((p) => p.name))));
  function expression(n, condition = false) {
    if (n.type === "NumericLiteral") return literal(n.value);
    if (n.type === "BooleanLiteral") return { code: n.value ? "64'd1" : "64'd0", kind: "bool" };
    if (n.type === "Identifier") {
      const value = env.get(n.name);
      if (!value) throw Error("Unbound source local: " + n.name);
      return value;
    }
    const field = member(n);
    if (field === "_logStateChanges") return { code: "64'd0", kind: "bool" };
    if (field) {
      if (!fields.has(field)) throw Error("Unsupported source field read: " + field);
      return { code: fields.get(field), kind: "num" };
    }
    if (
      n.type === "MemberExpression" &&
      !n.computed &&
      n.object.name === "State" &&
      states.has(n.property.name)
    )
      return literal(states.get(n.property.name));
    if (n.type === "UnaryExpression" && n.operator === "!")
      return { code: "(" + expression(n.argument).code + " == 0)", kind: "bool" };
    if (n.type === "UnaryExpression" && n.operator === "~")
      return { code: "jsInt(~(" + expression(n.argument).code + "))", kind: "num" };
    if (n.type === "LogicalExpression") {
      if (!condition || !["&&", "||"].includes(n.operator))
        throw Error("Unsupported value-valued source logic");
      return { code: "(" + truth(n.left) + n.operator + truth(n.right) + ")", kind: "bool" };
    }
    if (n.type === "BinaryExpression") {
      const left = expression(n.left),
        right = expression(n.right),
        op = n.operator;
      if (["===", "!=="].includes(op)) {
        if (left.kind !== right.kind)
          return { code: op === "===" ? "64'd0" : "64'd1", kind: "bool" };
        return {
          code: "(" + left.code + (op === "===" ? " == " : " != ") + right.code + ")",
          kind: "bool",
        };
      }
      if ([">=", "<", ">", "<="].includes(op)) {
        if (left.kind !== "num" || right.kind !== "num")
          throw Error("Changed source Number comparison");
        return {
          code: "($signed(" + left.code + ") " + op + " $signed(" + right.code + "))",
          kind: "bool",
        };
      }
      if (["&", "|", "^"].includes(op))
        return { code: "jsInt((" + left.code + ") " + op + " (" + right.code + "))", kind: "num" };
      if (op === "<<")
        return {
          code: "jsInt((" + left.code + ") << ((" + right.code + ") & 64'd31))",
          kind: "num",
        };
      if (op === ">>>")
        return {
          code: "(jsUint(" + left.code + ") >> ((" + right.code + ") & 64'd31))",
          kind: "num",
        };
      throw Error("Unsupported source Number operator: " + op);
    }
    if (n.type === "ConditionalExpression") {
      const a = expression(n.consequent),
        b = expression(n.alternate);
      if (a.kind !== b.kind) throw Error("Mixed source conditional types");
      return { code: "(" + truth(n.test) + " ? " + a.code + " : " + b.code + ")", kind: a.kind };
    }
    if (
      n.type === "CallExpression" &&
      n.callee.object?.name === "IntelFdc" &&
      n.callee.property?.name === "extractBits" &&
      n.arguments.length === 1
    )
      return call(
        extract,
        n.arguments.map((a) => expression(a)),
      );
    throw Error("Unsupported source expression: " + n.type);
  }
  function target(n) {
    const field = member(n);
    if (field && fields.has(field)) return fields.get(field);
    if (n.type === "Identifier" && env.get(n.name)?.mutable) return env.get(n.name).code;
    throw Error("Source write escapes delimiter fields");
  }
  function body(n, returned, result) {
    if (n.type === "BlockStatement") {
      const previous = new Map(env);
      for (const s of n.body) statement(s, returned, result);
      env = previous;
    } else statement(n, returned, result);
  }
  function statement(n, returned, result) {
    lines.push("if (" + returned + " == 0) begin");
    if (n.type === "VariableDeclaration") {
      if (!["let", "const"].includes(n.kind)) throw Error("Unsupported source declaration scope");
      for (const d of n.declarations) {
        if (d.id.type !== "Identifier" || !d.init) throw Error("Unsupported source local");
        const value = expression(d.init),
          code = allocate();
        env.set(d.id.name, { code, kind: value.kind, mutable: n.kind !== "const" });
        lines.push(code + " = " + value.code + ";");
      }
    } else if (n.type === "BlockStatement") body(n, returned, result);
    else if (n.type === "IfStatement") {
      lines.push("if (" + truth(n.test) + ") begin");
      body(n.consequent, returned, result);
      lines.push("end");
      if (n.alternate) {
        lines.push("else begin");
        body(n.alternate, returned, result);
        lines.push("end");
      }
    } else if (n.type === "BreakStatement") {
      if (n.label) throw Error("Unsupported labelled source break");
      lines.push(returned + " = 1;");
    } else if (n.type === "ReturnStatement") {
      if (!n.argument) throw Error("Unsupported void source return");
      const value = expression(n.argument);
      if (value.kind !== "num") throw Error("Changed extract return type");
      lines.push(result + " = " + value.code + ";", returned + " = 1;");
    } else if (n.type === "ThrowStatement") lines.push("trap = 1;", returned + " = 1;");
    else if (n.type === "ExpressionStatement") {
      const a = n.expression;
      if (a.type === "AssignmentExpression") {
        let value;
        if (a.operator === "=") value = expression(a.right);
        else if (["|=", "&=", "^=", "<<=", ">>>="].includes(a.operator))
          value = expression({
            type: "BinaryExpression",
            operator: a.operator.slice(0, -1),
            left: a.left,
            right: a.right,
          });
        else throw Error("Unsupported source assignment");
        if (value.kind !== "num") throw Error("Changed numeric delimiter field");
        lines.push(target(a.left) + " = " + value.code + ";");
      } else if (
        a.type === "UpdateExpression" &&
        a.operator === "++" &&
        ["_stateCount", "_numShifts"].includes(member(a.argument))
      ) {
        const t = target(a.argument);
        lines.push(
          t +
            " = " +
            (member(a.argument) === "_stateCount"
              ? "(" + t + " == 64'h20000000000000 ? " + t + " : " + t + " + 64'd1)"
              : t + " + 64'd1") +
            ";",
        );
      } else if (
        a.type === "CallExpression" &&
        member(a.callee) === "_setState" &&
        a.arguments.length === 1
      )
        call(
          setter,
          a.arguments.map((x) => expression(x)),
        );
      else if (a.type === "CallExpression" && member(a.callee) === "_log")
        a.arguments.forEach((v) => pureLog(v));
      else if (
        a.type === "CallExpression" &&
        member(a.callee) === "_byteCallbackReading" &&
        a.arguments.length === 2
      ) {
        const args = a.arguments.map((x) => expression(x));
        if (args.some((v) => v.kind !== "num")) throw Error("Changed source callback arguments");
        lines.push(
          "if (sourceState != 64'd" + states.get("checkIdMarker") + ") trap = 1;",
          "candidate = 1;",
          "dataByte = " + args[0].code + ";",
          "clockByte = " + args[1].code + ";",
          "candidateBit = currentBit;",
        );
      } else throw Error("Unsupported source effect: " + a.type);
    } else throw Error("Unsupported source statement: " + n.type);
    lines.push("end");
  }
  function call(method, args) {
    if (args.length !== method.params.length) throw Error("Changed source call arity");
    const previous = new Map(env),
      returned = allocate(),
      result = allocate();
    lines.push(returned + " = 0;", result + " = 0;");
    method.params.forEach((p, i) => {
      const code = allocate();
      lines.push(code + " = " + args[i].code + ";");
      env.set(p.name, { code, kind: args[i].kind, mutable: true });
    });
    if (method === shift) {
      statement(method.body.body[0], returned, result);
      const sw = method.body.body[1];
      lines.push("case (" + expression(sw.discriminant).code + ")");
      for (const name of ["syncingForId", "checkIdMarker"]) {
        const index = sw.cases.findIndex(
          (c) => c.test?.object?.name === "State" && c.test.property?.name === name,
        );
        if (index < 0) throw Error("Missing source delimiter branch");
        let selected = index;
        while (selected < sw.cases.length && sw.cases[selected].consequent.length === 0) selected++;
        if (selected === sw.cases.length) throw Error("Incomplete source switch group");
        let terminal = sw.cases[selected].consequent.at(-1);
        while (terminal?.type === "BlockStatement") terminal = terminal.body.at(-1);
        if (terminal?.type !== "BreakStatement" || terminal.label)
          throw Error("Unsupported source switch fallthrough");
        lines.push("64'd" + states.get(name) + ": begin");
        const branchEnv = new Map(env);
        for (const s of sw.cases[selected].consequent) statement(s, returned, result);
        env = branchEnv;
        lines.push("end");
      }
      lines.push("default: trap = 1;", "endcase");
    } else for (const s of method.body.body) statement(s, returned, result);
    env = previous;
    return { code: result, kind: "num" };
  }
  for (let i = 0; i < 16; i++) {
    const bit = i % 2 === 0 ? 15 - i / 2 : 7 - Math.floor(i / 2);
    lines.push(
      "currentBit = 4'd" + i + ";",
      "if (length > " + i + " && !candidate && !trap) begin",
    );
    call(shift, [{ code: "{63'd0,word[" + bit + "]}", kind: "bool" }]);
    lines.push("end");
  }
  return {
    states: Object.fromEntries(states),
    verilog: `module FdcSyncSource(
input[53:0]age,input[31:0]shift,input[4:0]count,input checking,input[15:0]word,input[4:0]length,
output[53:0]followingAge,output[31:0]followingShift,output[4:0]followingCount,output followingChecking,
output reg candidate,trap,output reg[7:0]clockByte,dataByte,output reg[3:0]candidateBit);
reg[63:0]sourceAge,sourceShift,sourceCount,sourceState;
reg[3:0]currentBit;
${locals.map((v) => "reg[63:0] " + v + ";").join("\n")}
function[63:0]jsInt(input[63:0]v);begin jsInt={{32{v[31]}},v[31:0]};end endfunction
function[63:0]jsUint(input[63:0]v);begin jsUint={32'd0,v[31:0]};end endfunction
assign followingAge=sourceAge[53:0];
assign followingShift=sourceShift[31:0];
assign followingCount=sourceCount[4:0];
assign followingChecking=sourceState==64'd${states.get("checkIdMarker")};
always @(*) begin
sourceAge={10'd0,age};sourceShift=jsInt({32'd0,shift});sourceCount={59'd0,count};
sourceState=checking?64'd${states.get("checkIdMarker")}:64'd${states.get("syncingForId")};
candidate=0;trap=0;clockByte=0;dataByte=0;candidateBit=0;currentBit=0;
${locals.map((v) => v + " = 0;").join("\n")}
${lines.join("\n")}
end
endmodule
`,
  };
}

export function syncFrames() {
  const maximum = 2 ** 53;
  const ages = [
    0,
    1,
    2,
    15,
    16,
    17,
    30,
    31,
    32,
    33,
    34,
    63,
    64,
    65,
    2 ** 31 - 1,
    2 ** 31,
    2 ** 32 - 1,
    2 ** 32,
    2 ** 32 + 1,
    maximum - 3,
    maximum - 2,
    maximum - 1,
    maximum,
  ];
  const frames = ages.map((age) => ({ age, shift: 0xa5a50000, count: 0, checking: false }));
  for (const shift of [0, 0x80000003]) frames.push({ age: 32, shift, count: 15, checking: false });
  for (const count of [0, 1, 2, 3, 7, 14, 15])
    for (const shift of [0, 3, 0xffffffff, 0x80000003])
      frames.push({ age: 0, shift, count, checking: true });
  return frames;
}

export async function originalFixtures(root, directory, states) {
  const { IntelFdc } = await import(pathToFileURL(root + "/src/intel-fdc.js"));
  const fake = {
    _logStateChanges: false,
    _setState: IntelFdc.prototype._setState,
    step: IntelFdc.prototype._shiftDataBit,
    _byteCallbackReading(data, clock) {
      if (this._state !== states.checkIdMarker || this.captured)
        throw Error("Source prefix escaped its callback boundary");
      this.captured = [clock, data, this.currentBit];
    },
  };
  function execute(frame, word, length) {
    fake._state = frame.checking ? states.checkIdMarker : states.syncingForId;
    fake._stateCount = frame.age;
    fake._shiftRegister = frame.shift | 0;
    fake._numShifts = frame.count;
    fake.captured = null;
    for (let i = 0; i < length && !fake.captured; i++) {
      fake.currentBit = i;
      const bit = i % 2 === 0 ? 15 - i / 2 : 7 - Math.floor(i / 2);
      fake.step(!!(word & (1 << bit)));
    }
    if (
      !Number.isInteger(fake._stateCount) ||
      fake._stateCount < 0 ||
      fake._stateCount > 2 ** 53 ||
      !Number.isInteger(fake._numShifts) ||
      fake._numShifts < 0 ||
      fake._numShifts > 15 ||
      ![states.syncingForId, states.checkIdMarker].includes(fake._state)
    )
      throw Error("Source delimiter prefix left its numeric frame");
    return [
      fake._stateCount,
      fake._shiftRegister >>> 0,
      fake._numShifts,
      +(fake._state === states.checkIdMarker),
      +!!fake.captured,
      ...(fake.captured ?? [0, 0, 0]),
    ];
  }
  const frames = syncFrames();
  fs.writeFileSync(
    directory + "/frames.tsv",
    frames.map((f) => [f.age, f.shift, f.count, +f.checking].join(" ")).join("\n") + "\n",
  );
  const file = fs.openSync(directory + "/original.bin", "w");
  try {
    for (const frame of frames) {
      const bytes = Buffer.alloc(65536 * 18);
      for (let word = 0; word < 65536; word++) {
        const result = execute(frame, word, 16),
          offset = word * 18;
        bytes.writeBigUInt64LE(BigInt(result[0]), offset);
        bytes.writeUInt32LE(result[1], offset + 8);
        for (let i = 2; i < result.length; i++) bytes[offset + 10 + i] = result[i];
      }
      fs.writeSync(file, bytes);
    }
  } finally {
    fs.closeSync(file);
  }
  const short = [];
  for (const [id, frame] of frames.entries())
    for (const word of [0, 0xffff, 0xff00, 0xc7fe, 0x40fb, 1, 0x8000])
      for (let length = 0; length < 16; length++)
        short.push([id, word, length, ...execute(frame, word, length)].join(" "));
  fs.writeFileSync(directory + "/short.tsv", short.join("\n") + "\n");
  return { frames: frames.length, word_cases: frames.length * 65536, short_cases: short.length };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, directory] = process.argv.slice(2);
  const source = fs.readFileSync(root + "/src/intel-fdc.js", "utf8");
  const lowered = lowerSync(source);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(directory + "/source.v", lowered.verilog);
  const fixtures = await originalFixtures(root, directory, lowered.states);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify({
      passed: true,
      ...fixtures,
      states: lowered.states,
      domain:
        "Original sync/check-ID delimiter prefix; dense integer age 0..2^53, partial count 0..15, logging disabled; stop at the first byte-callback boundary.",
    }) + "\n",
  );
}
