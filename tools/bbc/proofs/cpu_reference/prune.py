"""Remove only unreachable wire definitions from the structural adapter."""

import re


def prune(source):
    lines = source.splitlines()
    if not lines or lines[-1] != "endmodule" or "output trap);" not in lines[0]:
        raise ValueError("Changed structural adapter module interface")
    definitions = {}
    outputs = {}
    position = 1
    while position < len(lines) - 1:
        declaration = re.fullmatch(r"wire (?:signed )?\[\d+:0\] (ssa_\w+);", lines[position])
        if declaration:
            name = declaration[1]
            if name in definitions or position + 1 >= len(lines):
                raise ValueError("Duplicate/incomplete structural wire")
            assignment = re.fullmatch(r"assign " + re.escape(name) + r"=(.*);", lines[position + 1])
            if assignment is None:
                raise ValueError("Changed structural wire assignment")
            definitions[name] = (lines[position] + "\n" + lines[position + 1], assignment[1])
            position += 2
            continue
        assignment = re.fullmatch(r"assign (state_out|pins_out|trap)=(ssa_\w+);", lines[position])
        if assignment is None or assignment[1] in outputs:
            raise ValueError("Unsupported structural adapter statement")
        outputs[assignment[1]] = (assignment[2], lines[position])
        position += 1
    if set(outputs) != {"state_out", "pins_out", "trap"}:
        raise ValueError("Incomplete structural outputs")
    pending = [value[0] for value in outputs.values()]
    used = set()
    while pending:
        name = pending.pop()
        if name in used:
            continue
        if name not in definitions:
            raise ValueError("Undriven structural dependency: " + name)
        used.add(name)
        pending.extend(re.findall(r"\bssa_\w+\b", definitions[name][1]))
    result = "\n".join(
        [
            lines[0],
            *[text for name, (text, _) in definitions.items() if name in used],
            *[value[1] for value in outputs.values()],
            "endmodule",
            "",
        ]
    )
    return result, {
        "original_wires": len(definitions),
        "retained_wires": len(used),
        "original_bytes": len(source.encode()),
        "retained_bytes": len(result.encode()),
    }
