"""Lower the C adapter's explicit procedural subset to typed SSA wires.

This is a separate investigation adapter. Validate both its procedural
equivalence and its native C comparison before using it for new proofs.
"""

import re

from tools.bbc.proofs.cpu_reference import frontend


class Lowering:
    def __init__(self, translated):
        self.types = dict(translated.variables)
        self.types.update(state_out=frontend.Integer(256), pins_out=frontend.Integer(64))
        self.lines = [line.strip() for line in translated.lines]
        self.position = 0
        self.wires = []
        self.counter = 0

    def wire(self, name, expression):
        kind = self.types[name]
        version = f"ssa_{name}_{self.counter}"
        self.counter += 1
        self.wires.append(
            f"wire {'signed ' if kind.signed else ''}[{kind.width - 1}:0] {version};\nassign {version}={expression};"
        )
        return version

    def expression(self, text, environment):
        return re.sub(
            r"\b[A-Za-z_][A-Za-z_0-9]*\b", lambda match: environment.get(match[0], match[0]), text
        )

    def assignment(self, line, environment):
        match = re.fullmatch(r"(\w+)\s*=\s*(.*);", line)
        if match is None or match[1] not in self.types:
            raise ValueError("Unsupported adapter SSA assignment: " + line)
        environment[match[1]] = self.wire(match[1], self.expression(match[2], environment))

    def merge(self, before, branches, default):
        result = dict(before)
        changed = {
            name
            for branch in [default, *[env for _, env in branches]]
            for name in before
            if branch[name] != before[name]
        }
        for name in sorted(changed):
            expression = default[name]
            for condition, branch in reversed(branches):
                # Case labels are unique and therefore mutually exclusive.
                # A default-valued case needs no mux, even after another
                # case contributed a different value. Ifs have one branch.
                if branch[name] != default[name]:
                    expression = f"({condition}) ? ({branch[name]}) : ({expression})"
            result[name] = self.wire(name, expression)
        return result

    def merge_cases(self, before, branches, default):
        """Keep per-case differences sparse; decoder temporaries are local."""
        updates = {}
        for condition, changes in branches:
            for name, value in changes.items():
                updates.setdefault(name, []).append((condition, value))
        result = dict(before)
        for name in sorted(set(updates) | set(default)):
            fallback = default.get(name, before[name])
            # A changed default requires explicit unchanged alternatives.
            alternatives = (
                [(condition, changes.get(name, before[name])) for condition, changes in branches]
                if name in default
                else updates[name]
            )
            expression = fallback
            for condition, value in reversed(alternatives):
                if value != fallback:
                    expression = self.wire(name, f"({condition}) ? ({value}) : ({expression})")
            result[name] = self.wire(name, expression)
        return result

    def block(self, environment, stops):
        environment = dict(environment)
        while self.position < len(self.lines):
            line = self.lines[self.position]
            if line in stops:
                return environment
            self.position += 1
            match = re.fullmatch(r"if \((.*)\) begin", line)
            if match:
                condition = self.expression(match[1], environment)
                before = dict(environment)
                yes = self.block(before, {"end", "end else begin"})
                ending = self.lines[self.position]
                self.position += 1
                no = before
                if ending == "end else begin":
                    no = self.block(before, {"end"})
                    if self.lines[self.position] != "end":
                        raise ValueError("Missing SSA conditional end")
                    self.position += 1
                environment = self.merge(before, [(condition, yes)], no)
                continue
            match = re.fullmatch(r"if \((.*)\) (\w+\s*=.*;)", line)
            if match:
                condition = self.expression(match[1], environment)
                changed = dict(environment)
                self.assignment(match[2], changed)
                environment = self.merge(environment, [(condition, changed)], environment)
                continue
            match = re.fullmatch(r"case \((.*)\)", line)
            if match:
                selector = self.expression(match[1], environment)
                before = dict(environment)
                alternatives = []
                default = None
                labels = set()
                while self.position < len(self.lines) and self.lines[self.position] != "endcase":
                    label = re.fullmatch(r"(\d+'d\d+|default): begin", self.lines[self.position])
                    if label is None or label[1] in labels:
                        raise ValueError("Changed SSA case label")
                    labels.add(label[1])
                    self.position += 1
                    branch = self.block(before, {"end"})
                    changes = {
                        name: value for name, value in branch.items() if value != before[name]
                    }
                    self.position += 1
                    if label[1] == "default":
                        default = changes
                    else:
                        alternatives.append((f"({selector})==({label[1]})", changes))
                if default is None or self.position == len(self.lines):
                    raise ValueError("Missing SSA decoder default/end")
                self.position += 1
                environment = self.merge_cases(before, alternatives, default)
                continue
            match = re.fullmatch(r"\{\s*([^{}]+)\s*\}\s*=\s*state_in;", line)
            if match:
                names = [name.strip() for name in match[1].split(",")]
                if names != ["n_" + name for name, _ in frontend.FIELDS]:
                    raise ValueError("Changed source state packing")
                offset = 256
                for name in names:
                    width = self.types[name].width
                    offset -= width
                    environment[name] = self.wire(name, f"state_in[{offset + width - 1}:{offset}]")
                continue
            self.assignment(line, environment)
        if stops:
            raise ValueError("Unclosed SSA control block")
        return environment

    def generate(self):
        environment = {name: f"{kind.width}'d0" for name, kind in self.types.items()}
        final = self.block(environment, set())
        return (
            "module M6502ReferenceNext(input [255:0] state_in,input [63:0] pins_in,output [255:0] state_out,output [63:0] pins_out,output trap);\n"
            + "\n".join(self.wires)
            + "\n"
            + "\n".join(
                f"assign {name}={final[name]};" for name in ("state_out", "pins_out", "trap")
            )
            + "\nendmodule\n"
        )


def generate(ast):
    translated = frontend.Frontend(ast)
    translated.generate()
    return Lowering(translated).generate()
