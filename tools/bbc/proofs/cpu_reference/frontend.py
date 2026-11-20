"""Translate the checked C tick subset with explicit C integer widths.

This adapter deliberately rejects unsupported syntax. Generated equations
are an investigation artifact until separately checked against compiled C.
"""

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pycparser import c_ast, c_parser

from tools.validation.common import ROOT, digest, write_json


@dataclass(frozen=True)
class Integer:
    width: int
    signed: bool = False
    boolean: bool = False


INT = Integer(32, True)
TYPES = {
    "uint8_t": Integer(8),
    "int8_t": Integer(8, True),
    "uint16_t": Integer(16),
    "uint32_t": Integer(32),
    "uint64_t": Integer(64),
    "bool": Integer(1, boolean=True),
    "_Bool": Integer(1, boolean=True),
    "int": INT,
}
FIELDS = (
    ("IR", 16),
    ("PC", 16),
    ("AD", 16),
    ("A", 8),
    ("X", 8),
    ("Y", 8),
    ("S", 8),
    ("P", 8),
    ("PINS", 64),
    ("irq_pip", 16),
    ("branch_irq", 8),
    ("nmi_pip", 32),
    ("brk_flags", 8),
    ("bcd_enabled", 8),
    ("io_ddr", 8),
    ("io_out", 8),
    ("io_inp", 8),
    ("io_pins", 8),
)
FUNCTIONS = (
    "_m6502_adc",
    "_m6502_sbc",
    "_m6502_cmp",
    "_m6502_asl",
    "_m6502_lsr",
    "_m6502_rol",
    "_m6502_ror",
    "_m6502_bit",
    "_m6502_arr",
    "_m6502_sbx",
    "m6502_tick",
)


def extract(source):
    text = source.read_text()
    clean = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", lambda match: " " * len(match[0]), text)
    bodies = []
    for name in FUNCTIONS:
        match = re.search(
            r"(?:static inline )?(?:void|uint8_t|uint64_t) " + name + r"\([^;{}]*\)\s*\{", clean
        )
        if match is None:
            raise ValueError("Missing original C function: " + name)
        end = match.end()
        depth = 1
        while depth:
            depth += (clean[end] == "{") - (clean[end] == "}")
            end += 1
        bodies.append(text[match.start() : end])
    defines = "\n".join(
        line
        for line in text.splitlines()
        if re.match(
            r"#define (?:M6502_|M6510_|_SA\b|_GA\b|_SAD\b|_FETCH\b|_SD\b|_GD\b|_ON\b|_OFF\b|_RD\b|_WR\b|_NZ\b|_M6502_NZ\b)",
            line,
        )
    )
    prelude = """typedef unsigned char uint8_t;
typedef signed char int8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef _Bool bool;
#define false 0
#define true 1
#define assert(e) ref_assert(e)
#define _M6502_UNREACHABLE ref_assert(0)
typedef struct {uint16_t IR,PC,AD;uint8_t A,X,Y,S,P;uint64_t PINS;
uint16_t irq_pip;uint8_t branch_irq;uint32_t nmi_pip;
uint8_t brk_flags,bcd_enabled,io_ddr,io_out,io_inp,io_pins;} m6502_t;
"""
    return prelude + defines + "\n" + "\n".join(bodies)


def promote(kind):
    return INT if kind.width < 32 else kind


def common(a, b):
    a, b = promote(a), promote(b)
    if a.width == b.width:
        return Integer(a.width, a.signed and b.signed)
    wide, narrow = (a, b) if a.width > b.width else (b, a)
    return Integer(wide.width, wide.signed and wide.width > narrow.width)


def type_of(node):
    while isinstance(node, (c_ast.TypeDecl, c_ast.Typename, c_ast.Decl)):
        node = node.type
    if (
        not isinstance(node, c_ast.IdentifierType)
        or len(node.names) != 1
        or node.names[0] not in TYPES
    ):
        raise ValueError("Unsupported C integer type: " + repr(node))
    return TYPES[node.names[0]]


def constant(node):
    value = re.sub(r"[uUlL]+$", "", node.value)
    number = int(
        value,
        16
        if value.lower().startswith("0x")
        else 8
        if value.startswith("0") and len(value) > 1
        else 10,
    )
    lower = node.value.lower()
    unsigned = "u" in lower
    width = 64 if "l" in lower or number > 0xFFFFFFFF else 32
    if not unsigned and width == 32 and number > 0x7FFFFFFF:
        if value.lower().startswith("0x") or (value.startswith("0") and len(value) > 1):
            unsigned = True
        else:
            width = 64
    return f"{width}'h{number:x}", Integer(width, not unsigned)


class Frontend:
    def __init__(self, ast):
        self.functions = {
            node.decl.name: node for node in ast.ext if isinstance(node, c_ast.FuncDef)
        }
        if set(self.functions) != set(FUNCTIONS):
            raise ValueError("Changed extracted function inventory")
        self.lines = []
        self.variables = {}
        self.counter = 0
        self.indent = 0
        self.env = {}
        self.return_stack = []
        self.fields = {name: ("n_" + name, Integer(width)) for name, width in FIELDS}
        for name, kind in self.fields.values():
            self.variables[name] = kind
        self.variables["pins"] = Integer(64)
        self.variables["trap"] = Integer(1, boolean=True)

    def emit(self, text):
        self.lines.append("  " * self.indent + text)

    def temporary(self, kind, label="v"):
        name = f"{label}_{self.counter}"
        self.counter += 1
        self.variables[name] = kind
        return name, kind

    def assign(self, destination, value):
        name, kind = destination
        self.emit(name + " = " + self.convert(value, kind) + ";")

    def convert(self, value, target):
        expr, source = value
        if target.boolean:
            return f"(({expr}) != 0)"
        if source.width < target.width:
            # Conversion of a negative signed source to an unsigned wider
            # type also sign-extends before interpreting the result modulo 2^N.
            extension = source.width and source.signed
            expr = (
                f"{target.width}'($signed({expr}))"
                if extension
                else f"{target.width}'($unsigned({expr}))"
            )
        elif source.width > target.width:
            expr = f"{target.width}'({expr})"
        return f"$signed({expr})" if target.signed else f"$unsigned({expr})"

    def save(self, value, label="v"):
        temporary = self.temporary(value[1], label)
        self.assign(temporary, value)
        return temporary

    def lvalue(self, node):
        if isinstance(node, c_ast.StructRef):
            if (
                node.type != "->"
                or not isinstance(node.name, c_ast.ID)
                or self.env.get(node.name.name) != "cpu"
            ):
                raise ValueError("Unsupported C pointer member")
            if node.field.name not in self.fields:
                raise ValueError("Unexpected C CPU field")
            return self.fields[node.field.name]
        if isinstance(node, c_ast.ID) and isinstance(self.env.get(node.name), tuple):
            return self.env[node.name]
        raise ValueError("Unsupported C lvalue: " + repr(node))

    def binary(self, op, left, right):
        if op in ("<<", ">>"):
            kind = promote(left[1])
            a = self.convert(left, kind)
            b = self.convert(right, promote(right[1]))
            operator = ">>>" if op == ">>" and kind.signed else op
            result = (f"({a} {operator} {b})", kind)
        else:
            kind = common(left[1], right[1])
            a = self.convert(left, kind)
            b = self.convert(right, kind)
            result = (
                (f"{{31'b0,({a} {op} {b})}}", INT)
                if op in ("==", "!=", "<", ">", "<=", ">=")
                else (f"({a} {op} {b})", kind)
            )
        return self.save(result)

    def expression(self, node):
        if isinstance(node, c_ast.Constant):
            return constant(node)
        if isinstance(node, (c_ast.ID, c_ast.StructRef)):
            return self.lvalue(node)
        if isinstance(node, c_ast.Cast):
            return self.save(
                (
                    self.convert(self.expression(node.expr), type_of(node.to_type)),
                    type_of(node.to_type),
                )
            )
        if isinstance(node, c_ast.Assignment):
            destination = self.lvalue(node.lvalue)
            value = self.expression(node.rvalue)
            if node.op != "=":
                value = self.binary(node.op[:-1], destination, value)
            self.assign(destination, value)
            return destination
        if isinstance(node, c_ast.UnaryOp):
            if node.op in ("p++", "p--", "++", "--"):
                destination = self.lvalue(node.expr)
                old = self.save(destination, "old")
                self.assign(
                    destination, self.binary("+" if "+" in node.op else "-", old, ("32'd1", INT))
                )
                return old if node.op.startswith("p") else destination
            value = self.expression(node.expr)
            if node.op == "!":
                return self.save((f"{{31'b0,(({value[0]}) == 0)}}", INT))
            if node.op not in ("~", "-", "+"):
                raise ValueError("Unsupported C unary operator")
            kind = promote(value[1])
            return self.save((f"({node.op}{self.convert(value, kind)})", kind))
        if isinstance(node, c_ast.BinaryOp):
            left = self.save(self.expression(node.left))
            if node.op in ("&&", "||"):
                result = self.save((f"{{31'b0,({left[0]} != 0)}}", INT))
                self.emit(f"if ({result[0]} {'!=' if node.op == '&&' else '=='} 0) begin")
                self.indent += 1
                right = self.expression(node.right)
                self.assign(result, (f"{{31'b0,({right[0]} != 0)}}", INT))
                self.indent -= 1
                self.emit("end")
                return result
            right = self.expression(node.right)
            return self.binary(node.op, left, right)
        if isinstance(node, c_ast.TernaryOp):
            # Determine the result type in an isolated dry translation; only
            # selected-branch assignments appear in the actual transition.
            condition = self.expression(node.cond)
            saved_lines, saved_env = list(self.lines), dict(self.env)
            a = self.expression(node.iftrue)
            b = self.expression(node.iffalse)
            self.lines = saved_lines
            self.env = saved_env
            result = self.temporary(common(a[1], b[1]))
            self.emit(f"if ({condition[0]} != 0) begin")
            self.indent += 1
            self.assign(result, self.expression(node.iftrue))
            self.indent -= 1
            self.emit("end else begin")
            self.indent += 1
            self.assign(result, self.expression(node.iffalse))
            self.indent -= 1
            self.emit("end")
            return result
        if isinstance(node, c_ast.FuncCall):
            if not isinstance(node.name, c_ast.ID):
                raise ValueError("Unsupported indirect C call")
            args = list(node.args.exprs) if node.args else []
            if node.name.name == "ref_assert":
                if len(args) != 1:
                    raise ValueError("Unexpected original assertion")
                test = self.expression(args[0])
                self.emit(f"if ({test[0]} == 0) trap = 1'b1;")
                return ("32'd0", INT)
            return self.call(node.name.name, args)
        raise ValueError("Unsupported C expression: " + type(node).__name__)

    def case_value(self, node):
        if isinstance(node, c_ast.Constant):
            return int(re.sub(r"[uUlL]+$", "", node.value), 0)
        if isinstance(node, c_ast.BinaryOp):
            a, b = self.case_value(node.left), self.case_value(node.right)
            if node.op == "<<":
                return a << b
            if node.op == "|":
                return a | b
        raise ValueError("Unsupported C case constant")

    def has_return(self, node):
        if node is None:
            return False
        return isinstance(node, c_ast.Return) or any(
            self.has_return(child) for _, child in node.children()
        )

    def statement(self, node, guarded=False):
        if node is None:
            return
        if guarded:
            returned = self.return_stack[-1][0]
            self.emit(f"if (!{returned}) begin")
            self.indent += 1
            self.statement(node)
            self.indent -= 1
            self.emit("end")
            return
        if isinstance(node, c_ast.Compound):
            env = dict(self.env)
            may_have_returned = False
            for statement in node.block_items or []:
                self.statement(statement, may_have_returned)
                may_have_returned |= self.has_return(statement)
            self.env = env
            return
        returned = self.return_stack[-1][0]
        if isinstance(node, c_ast.Decl):
            kind = type_of(node)
            value = self.temporary(kind, node.name)
            self.env[node.name] = value
            if node.init is not None:
                self.assign(value, self.expression(node.init))
        elif isinstance(node, c_ast.If):
            test = self.expression(node.cond)
            self.emit(f"if ({test[0]} != 0) begin")
            self.indent += 1
            self.statement(node.iftrue)
            self.indent -= 1
            if node.iffalse is not None:
                self.emit("end else begin")
                self.indent += 1
                self.statement(node.iffalse)
                self.indent -= 1
            self.emit("end")
        elif isinstance(node, c_ast.Return):
            result = self.return_stack[-1][1]
            if node.expr is not None:
                if result is None:
                    raise ValueError("Value return from void C function")
                self.assign(result, self.expression(node.expr))
            self.emit(returned + " = 1'b1;")
        elif isinstance(node, c_ast.Switch):
            value = self.expression(node.cond)
            self.emit(f"case ({value[0]})")
            self.indent += 1
            cases = node.stmt.block_items
            for case in cases:
                if not isinstance(case, (c_ast.Case, c_ast.Default)):
                    raise ValueError("Unexpected C switch declaration")
                self.emit(
                    (
                        f"{value[1].width}'d{self.case_value(case.expr)}"
                        if isinstance(case, c_ast.Case)
                        else "default"
                    )
                    + ": begin"
                )
                self.indent += 1
                statements = case.stmts or []
                if not isinstance(case, c_ast.Default) and (
                    not statements or not isinstance(statements[-1], c_ast.Break)
                ):
                    raise ValueError("Unsupported C case fallthrough")
                for statement in statements:
                    if not isinstance(statement, c_ast.Break):
                        self.statement(statement)
                self.indent -= 1
                self.emit("end")
            self.indent -= 1
            self.emit("endcase")
        elif isinstance(node, c_ast.EmptyStatement):
            pass
        elif isinstance(node, (c_ast.Assignment, c_ast.UnaryOp, c_ast.FuncCall)):
            self.expression(node)
        else:
            raise ValueError("Unsupported C statement: " + type(node).__name__)

    def call(self, name, args):
        if name not in self.functions:
            raise ValueError("Unknown original C helper: " + name)
        function = self.functions[name]
        params = function.decl.type.args.params
        if len(args) != len(params):
            raise ValueError("C call argument count mismatch")
        caller = dict(self.env)
        bound = {}
        for param, arg in zip(params, args, strict=True):
            if isinstance(param.type, c_ast.PtrDecl):
                if not isinstance(arg, c_ast.ID) or caller.get(arg.name) != "cpu":
                    raise ValueError("Unsupported C pointer argument")
                bound[param.name] = "cpu"
            else:
                temporary = self.temporary(type_of(param), param.name)
                self.assign(temporary, self.expression(arg))
                bound[param.name] = temporary
        result_type = function.decl.type.type
        is_void = (
            isinstance(result_type, c_ast.TypeDecl)
            and isinstance(result_type.type, c_ast.IdentifierType)
            and result_type.type.names == ["void"]
        )
        result = None if is_void else self.temporary(type_of(result_type), "result")
        returned = self.temporary(Integer(1, boolean=True), "returned")[0]
        self.emit(returned + " = 1'b0;")
        self.env = bound
        self.return_stack.append((returned, result))
        self.statement(function.body)
        self.return_stack.pop()
        self.env = caller
        return result or ("32'd0", INT)

    def generate(self):
        self.emit("{ " + ",".join(name for name, _ in self.fields.values()) + " } = state_in;")
        self.emit("pins = pins_in;")
        self.emit("trap = 1'b0;")
        self.env = {"cpu": "cpu", "incoming": ("pins", Integer(64))}
        result = self.call("m6502_tick", [c_ast.ID("cpu"), c_ast.ID("incoming")])
        self.emit("pins_out = " + result[0] + ";")
        self.emit("state_out = {" + ",".join(name for name, _ in self.fields.values()) + "};")
        declarations = []
        for name, kind in self.variables.items():
            declarations.append(
                f"reg {'signed ' if kind.signed else ''}[{kind.width - 1}:0] {name};"
            )
        defaults = "\n".join(
            f"{name}=0;"
            for name in self.variables
            if name not in {"pins", "trap", *(n for n, _ in self.fields.values())}
        )
        return (
            "module M6502ReferenceNext(input [255:0] state_in,input [63:0] pins_in,output reg [255:0] state_out,output reg [63:0] pins_out,output reg trap);\n"
            + "\n".join(line for line in declarations if not line.endswith(" trap;"))
            + "\nalways @* begin\n"
            + defaults
            + "\n"
            + "\n".join(self.lines)
            + "\nend\nendmodule\n"
        )


def generate(source, directory):
    directory.mkdir(parents=True, exist_ok=True)
    before = digest(source)
    extracted = directory / "extracted.c"
    extracted.write_text(extract(source))
    expanded = directory / "expanded.c"
    process = subprocess.run(
        ["gcc", "-E", "-P", str(extracted)], capture_output=True, text=True, check=True, timeout=30
    )
    expanded.write_text(process.stdout)
    ast = c_parser.CParser().parse(process.stdout)
    frontend = Frontend(ast)
    generated = directory / "M6502ReferenceNext.v"
    generated.write_text(frontend.generate())
    if digest(source) != before:
        raise ValueError("Original C source changed")
    report = {
        "generated": True,
        "validated": False,
        "source_sha256": before,
        "frontend_sha256": digest(Path(__file__)),
        "generated_sha256": digest(generated),
        "fields": FIELDS,
        "variables": len(frontend.variables),
        "scope": "Investigation-only integer-width-explicit translation of the extracted maintained C tick/helper bodies. Not yet a validated source translation or equivalence proof.",
    }
    write_json(directory / "generation.json", report)
    return generated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "build/bbc/reference-cycle/m6502.h")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "build/bbc/cpu-c-reference-transpiler"
    )
    args = parser.parse_args()
    print(generate(args.source, args.output))


if __name__ == "__main__":
    main()
