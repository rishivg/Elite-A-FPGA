"""Check dead-wire removal by identity and acyclic dependency closure."""

import re


def declarations(text):
    lines = text.splitlines()
    if (
        not lines
        or not lines[0].startswith("module M6502ReferenceNext(")
        or lines[-1] != "endmodule"
    ):
        raise ValueError("Changed structural module boundary")
    wires, roots = {}, {}
    index = 1
    while index < len(lines) - 1:
        row = lines[index]
        if row.startswith("wire "):
            declaration = re.fullmatch(r"wire (?:signed )?\[\d+:0\] (\w+);", row)
            if not declaration or index + 1 >= len(lines) - 1:
                raise ValueError("Malformed structural declaration")
            name = declaration[1]
            assignment = re.fullmatch(r"assign (\w+)=(.*);", lines[index + 1])
            if not assignment or assignment[1] != name or name in wires:
                raise ValueError("Duplicate or missing structural driver")
            expression = assignment[2]
            tokens = re.findall(
                r"[A-Za-z_$][A-Za-z0-9_$]*",
                re.sub(r"\d+'s?[bdh][0-9a-fx_z]+", "", expression, flags=re.I),
            )
            dependencies = set(tokens) - {"state_in", "pins_in", "$signed", "$unsigned"}
            if any(name not in wires for name in dependencies):
                raise ValueError("Non-acyclic or undeclared structural dependency")
            wires[name] = (row, lines[index + 1], dependencies)
            index += 2
        else:
            assignment = re.fullmatch(r"assign (state_out|pins_out|trap)=(\w+);", row)
            if not assignment or assignment[1] in roots or assignment[2] not in wires:
                raise ValueError("Changed structural output binding")
            roots[assignment[1]] = assignment[2]
            index += 1
    if set(roots) != {"state_out", "pins_out", "trap"}:
        raise ValueError("Incomplete structural outputs")
    return lines[0], wires, roots


def verify(full, pruned):
    header, original, roots = declarations(full)
    reduced_header, reduced, reduced_roots = declarations(pruned)
    if header != reduced_header or roots != reduced_roots:
        raise ValueError("Pruning changed an input/output interface")
    for name, definition in reduced.items():
        if original.get(name) != definition:
            raise ValueError("Pruning changed a retained equation or type")
    pending, reachable = list(roots.values()), set()
    while pending:
        name = pending.pop()
        if name not in reachable:
            if name not in reduced:
                raise ValueError("Pruning removed an output dependency")
            reachable.add(name)
            pending.extend(original[name][2])
    if reachable != set(reduced):
        raise ValueError("Pruned model retains unexplained equations")
    return {
        "original_definitions": len(original),
        "retained_definitions": len(reduced),
        "output_bits": 321,
        "input_bits": 320,
        "derivation": "Each retained wire has the identical width, signedness and equation. Its dependencies precede it and are retained unchanged. Induction over this acyclic graph gives the same value under every input; all three output roots and the interface are identical. Deleted definitions are outside every output dependency cone.",
    }
