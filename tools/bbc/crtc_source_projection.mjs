// Lower the scalar CRTC projection of the original character step.
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { parsers } from "../../node_modules/prettier/plugins/babel.mjs";

export function lower(source) {
  const ast = parsers.babel.parse(source);
  const classes = ast.program.body.filter(
    (node) => node.type === "ExportNamedDeclaration" && node.declaration?.id?.name === "Video",
  );
  if (classes.length !== 1) throw Error("Expected one original Video class");
  const videoClass = classes[0].declaration;
  const methods = new Map(videoClass.body.body.map((method) => [method.key.name, method]));
  if (methods.size !== videoClass.body.body.length) throw Error("Duplicate original Video method");
  for (const name of ["polltime", "endOfScanline", "endOfCharacterLine", "endOfFrame"])
    if (!methods.has(name)) throw Error("Missing original method " + name);
  if (
    (source.match(/export const VDISPENABLE = 1 << 0;/g) ?? []).length !== 1 ||
    (source.match(/this.cursorFlashMask = new Uint8Array\(\[0x00, 0x00, 0x08, 0x10\]\);/g) ?? [])
      .length !== 1
  )
    throw Error("Changed original display bit or cursor mask table");
  const fields = [
    ["horizCounter", 8, "horizontal"],
    ["vertCounter", 7, "row"],
    ["scanlineCounter", 5, "raster"],
    ["inVertAdjust", 1, "adjusting"],
    ["vertAdjustCounter", 5, "adjust"],
    ["lineStartAddr", 14, "lineAddress"],
    ["nextLineStartAddr", 14, "nextLineAddress"],
    ["addr", 14, "address"],
    ["inVSync", 1, "sync"],
    ["vpulseCounter", 4, "syncLines"],
    ["hadVSyncThisRow", 1, "hadSync"],
    ["endOfMainLatched", 1, "endMain"],
    ["checkVertAdjust", 1, "checkAdjust"],
    ["endOfVertAdjustLatched", 1, "endAdjust"],
    ["endOfFrameLatched", 1, "endDummy"],
    ["frameCount", 5, "fieldCount"],
    ["cursorOn", 1, "cursorOn"],
    ["cursorOff", 1, "cursorOff"],
    ["cursorOnThisFrame", 1, "cursorVisible"],
    ["doEvenFrameLogic", 1, "!fieldOdd"],
    ["ra0", 1, "rasterPin0"],
    ["dispEnabled", 1, "verticalActive"],
    ["firstScanline", 1, "firstLine"],
  ];
  const tracked = new Set(fields.map(([name]) => name));
  const helpers = new Set(["endOfScanline", "endOfCharacterLine", "endOfFrame"]);
  const constants = { VDISPENABLE: 1, SCANLINEDISPENABLE: 8 };
  function member(node) {
    return node?.type === "MemberExpression" &&
      node.object.type === "ThisExpression" &&
      !node.computed
      ? node.property.name
      : null;
  }
  function callName(node) {
    return node?.type === "CallExpression" ? member(node.callee) : null;
  }
  function displayCall(node) {
    return (
      ["dispEnableSet", "dispEnableClear"].includes(callName(node)) &&
      node.arguments.length === 1 &&
      node.arguments[0].name === "VDISPENABLE"
    );
  }
  function raCall(node) {
    return (
      node?.type === "CallExpression" &&
      node.callee.type === "MemberExpression" &&
      member(node.callee.object) === "teletext" &&
      node.callee.property.name === "setRA0"
    );
  }
  function children(node) {
    return Object.entries(node)
      .filter(([key]) => !["loc", "start", "end", "comments"].includes(key))
      .flatMap(([, value]) => (Array.isArray(value) ? value : [value]))
      .filter((value) => value && typeof value === "object" && value.type);
  }
  function checkHelperCalls(node) {
    if (
      node.type === "CallExpression" &&
      !helpers.has(callName(node)) &&
      !["dispEnableSet", "dispEnableClear"].includes(callName(node)) &&
      !raCall(node)
    )
      throw Error("Unsupported call in original CRTC helper");
    children(node).forEach(checkHelperCalls);
  }
  for (const helper of helpers) checkHelperCalls(methods.get(helper).body);
  function writes(node) {
    if (!node) return false;
    if (node.type === "AssignmentExpression" || node.type === "UpdateExpression") {
      const target = node.left ?? node.argument;
      // Scalar locals control the retained statements; retain their updates.
      if (
        (target.type === "Identifier" &&
          ["vSyncEnding", "vSyncStarting", "endOfFrame", "externalScanline"].includes(
            target.name,
          )) ||
        tracked.has(member(target))
      )
        return true;
    }
    if (helpers.has(callName(node)) || displayCall(node) || raCall(node)) return true;
    return children(node).some(writes);
  }
  function slice(node) {
    if (node.type === "BlockStatement")
      return liveDeclarations({ ...node, body: node.body.map(slice).filter(Boolean) });
    if (node.type === "IfStatement") {
      const consequent = slice(node.consequent),
        alternate = node.alternate && slice(node.alternate);
      const nonempty = (part) => part && (part.type !== "BlockStatement" || part.body.length);
      return nonempty(consequent) || nonempty(alternate)
        ? {
            ...node,
            consequent: nonempty(consequent) ? consequent : { type: "BlockStatement", body: [] },
            alternate,
          }
        : null;
    }
    if (node.type === "VariableDeclaration") return node;
    return writes(node) ? node : null;
  }
  function liveDeclarations(block) {
    // Drop unused declarations, including rendering-only locals in polltime.
    const used = new Set();
    function read(node) {
      if (node.type === "Identifier") used.add(node.name);
      children(node).forEach(read);
    }
    block.body.filter((node) => node.type !== "VariableDeclaration").forEach(read);
    let changed;
    do {
      const size = used.size;
      for (const statement of block.body)
        if (statement.type === "VariableDeclaration")
          for (const declaration of statement.declarations)
            if (used.has(declaration.id.name)) read(declaration.init);
      changed = size !== used.size;
    } while (changed);
    return {
      ...block,
      body: block.body.flatMap((node) => {
        if (node.type !== "VariableDeclaration") return [node];
        const declarations = node.declarations.filter((declaration) =>
          used.has(declaration.id.name),
        );
        return declarations.length ? [{ ...node, declarations }] : [];
      }),
    };
  }
  let serial = 0;
  const locals = new Set();
  function expression(node, names) {
    if (node.type === "NumericLiteral") {
      if (!Number.isInteger(node.value) || node.value < 0 || node.value > 0xffffffff)
        throw Error("Unsupported JS numeric literal");
      return `32'd${node.value}`;
    }
    if (node.type === "BooleanLiteral") return node.value ? "32'd1" : "32'd0";
    if (node.type === "Identifier") {
      if (node.name in constants) return `32'd${constants[node.name]}`;
      if (!names.has(node.name)) throw Error("Unbound scalar " + node.name);
      return names.get(node.name);
    }
    const field = member(node);
    if (field) {
      if (
        !tracked.has(field) &&
        !["interlacedSyncAndVideo", "inDummyRaster", "vpulseWidth"].includes(field)
      )
        throw Error("Untracked scalar read " + field);
      return field;
    }
    if (node.type === "MemberExpression" && node.computed) {
      if (member(node.object) === "regs" && node.property.type === "NumericLiteral")
        return `r${node.property.value}`;
      if (member(node.object) === "cursorFlashMask") {
        const index = expression(node.property, names);
        return `(${index}==32'd2 ? 32'd8 : ${index}==32'd3 ? 32'd16 : 32'd0)`;
      }
    }
    if (node.type === "UnaryExpression" && ["!", "~"].includes(node.operator))
      return `(${node.operator}${expression(node.argument, names)})`;
    if (["BinaryExpression", "LogicalExpression"].includes(node.type)) {
      const operators = {
        "===": "==",
        "!==": "!=",
        ">>>": ">>",
        "+": "+",
        "&": "&",
        "|": "|",
        "<<": "<<",
        "&&": "&&",
        "||": "||",
      };
      if (!(node.operator in operators)) throw Error("Unsupported operator " + node.operator);
      return `(${expression(node.left, names)} ${operators[node.operator]} ${expression(node.right, names)})`;
    }
    throw Error("Unsupported scalar expression " + node.type);
  }
  function emit(block, parentNames = new Map()) {
    block = liveDeclarations(block);
    const names = new Map(parentNames);
    const lines = [];
    for (const node of block.body) {
      if (node.type === "VariableDeclaration") {
        for (const declaration of node.declarations) {
          const name = `local_${serial++}_${declaration.id.name}`;
          names.set(declaration.id.name, name);
          locals.add(name);
          lines.push(`${name}=${expression(declaration.init, names)};`);
        }
      } else if (node.type === "IfStatement") {
        const asBlock = (part) =>
          part.type === "BlockStatement" ? part : { type: "BlockStatement", body: [part] };
        lines.push(
          `if(${expression(node.test, names)})begin`,
          ...emit(asBlock(node.consequent), names),
          "end",
        );
        if (node.alternate)
          lines.push("else begin", ...emit(asBlock(node.alternate), names), "end");
      } else if (node.type === "ExpressionStatement") {
        const value = node.expression,
          method = callName(value);
        if (helpers.has(method)) {
          if (value.arguments.length) throw Error("Changed helper arguments");
          if (method === "endOfFrame") lines.push("frame=1;");
          lines.push(...emit(slice(methods.get(method).body)));
        } else if (displayCall(value)) {
          lines.push(
            method === "dispEnableSet"
              ? "dispEnabled=dispEnabled|32'd1;"
              : "dispEnabled=dispEnabled&~32'd1;",
          );
        } else if (raCall(value)) {
          lines.push(`ra0=${expression(value.arguments[0], names)};`);
        } else if (value.type === "AssignmentExpression") {
          const target = expression(value.left, names),
            right = expression(value.right, names);
          if (!["=", "&="].includes(value.operator)) throw Error("Unsupported assignment");
          lines.push(`${target}=${value.operator === "=" ? right : `(${target}&${right})`};`);
        } else if (value.type === "UpdateExpression" && value.operator === "++") {
          const target = expression(value.argument, names);
          lines.push(`${target}=${target}+32'd1;`);
        } else throw Error("Unsupported retained statement " + value.type);
      } else throw Error("Unsupported retained statement " + node.type);
    }
    return lines;
  }
  const loop = methods.get("polltime").body.body.find((node) => node.type === "WhileStatement");
  const body = emit(slice(loop.body));
  const width = fields.reduce((sum, [, bits]) => sum + bits, 0);
  let offset = width;
  const initial = fields.map(([name, bits]) => {
    offset -= bits;
    return `${name}={{${32 - bits}{1'b0}},state[${offset + bits - 1}:${offset}]};`;
  });
  const result = fields.map(([name, bits]) => `${name}[${bits - 1}:0]`);
  const regs = Array.from(
    { length: 14 },
    (_, i) => `wire [31:0] r${i}={24'd0,registers[${i * 8 + 7}:${i * 8}]};`,
  ).join("\n");
  const generated = `module CrtcSourceNext(input [${width - 1}:0] state,input [111:0] registers,output [${width - 1}:0] nextState,output reg frame);
${regs}
reg [31:0] ${[...fields.map(([name]) => name), "interlacedSyncAndVideo", "inDummyRaster", "vpulseWidth", ...locals].join(",")};
always @* begin
${initial.join("\n")}
interlacedSyncAndVideo=(r8&32'd3)==32'd3;
inDummyRaster=endOfFrameLatched;
vpulseWidth=(r3>>4)&32'd15;
frame=0;
${[...locals].map((name) => name + "=0;").join("\n")}
${body.join("\n")}
end
assign nextState={${result.join(",")}};
endmodule
`;
  return {
    verilog: generated,
    projection: { width, fields, sourceMethods: [...helpers, "polltime"] },
  };
}

async function main() {
  const [root, directory] = process.argv.slice(2);
  const lowered = lower(fs.readFileSync(root + "/src/video.js", "utf8"));
  const { fields, width } = lowered.projection;
  fs.writeFileSync(directory + "/source.v", lowered.verilog);
  fs.writeFileSync(
    directory + "/projection.json",
    JSON.stringify(lowered.projection, null, 2) + "\n",
  );

  // Execute the unchanged methods; only reference fixture state is assigned.
  const { Video } = await import(pathToFileURL(root + "/src/video.js"));
  const video = new Video(false, new Uint32Array(1024 * 625), () => {});
  video.cpu = { videoRead: () => 0 };
  video.sysvia = { setVBlankInt: () => {} };
  let seed = 0x6845c0de;
  function random() {
    seed ^= seed << 13;
    seed ^= seed >>> 17;
    seed ^= seed << 5;
    return seed >>> 0;
  }
  function pack(values) {
    return fields
      .reduce(
        (acc, [name, bits]) =>
          (acc << BigInt(bits)) | (BigInt(Number(values[name])) & ((1n << BigInt(bits)) - 1n)),
        0n,
      )
      .toString(16);
  }
  fs.writeFileSync(
    directory + "/initial.json",
    JSON.stringify({ state: pack({ ...video, ra0: video.teletext.levelRA0 }) }) + "\n",
  );
  let fixtures = "";
  const count = 65536;
  for (let trial = 0; trial < count; trial++) {
    const values = Object.fromEntries(
      fields.map(([name, bits]) => [name, random() & (2 ** bits - 1)]),
    );
    for (let i = 0; i < 14; i++) video.regs[i] = random() & 255;
    // Exercise equality boundaries alongside arbitrary live register bytes.
    if (trial & 1) values.horizCounter = video.regs[0];
    if (trial & 2) values.scanlineCounter = video.regs[9] & 31;
    if (trial & 4) values.vertCounter = video.regs[7] & 127;
    if (trial & 8) values.vertAdjustCounter = video.regs[5] & 31;
    let registers = 0n;
    for (let i = 13; i >= 0; i--) registers = (registers << 8n) | BigInt(video.regs[i]);
    Object.assign(video, values, {
      bitmapX: -9999,
      bitmapY: -1,
      halfClock: false,
      inHSync: false,
      cursorDrawIndex: 0,
      interlacedSyncAndVideo: (video.regs[8] & 3) === 3,
      inDummyRaster: !!values.endOfFrameLatched,
      vpulseWidth: video.regs[3] >>> 4,
    });
    video.teletext.levelRA0 = !!values.ra0;
    const before = pack(values);
    video.polltime(1);
    const after = { ...video, ra0: video.teletext.levelRA0 };
    fixtures += `${before} ${registers.toString(16)} ${pack(after)}\n`;
  }
  fs.writeFileSync(directory + "/fixtures.tsv", fixtures);
  console.log(`Original CRTC projection: ${width} bits; ${count} independent fixtures`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
