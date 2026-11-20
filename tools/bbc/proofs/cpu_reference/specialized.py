"""Propagate a bounded query's existing constants into free symbolic nets."""

import copy


def substitute(graph, top, bindings):
    """Replace whole anyconst/anyseq outputs, preserving every other driver.

    This is only for a query already fixing these signals. It must not be used
    to narrow an arbitrary-input history proof. Return a copy for subsequent
    Yosys optimization; the original graph remains the audit input.
    """
    result = copy.deepcopy(graph)
    module = result["modules"][top]
    replacements, removed = {}, set()
    for name, value in bindings.items():
        bits = module["netnames"][name]["bits"]
        if (
            type(value) is not int
            or not 0 <= value < 1 << len(bits)
            or not bits
            or any(type(bit) is not int for bit in bits)
            or len(set(bits)) != len(bits)
            or set(bits) & replacements.keys()
        ):
            raise ValueError("Invalid or overlapping symbolic binding: " + name)
        drivers = [
            (cell_name, cell, port)
            for cell_name, cell in module["cells"].items()
            for port, direction in cell["port_directions"].items()
            if direction == "output" and set(bits) & set(cell["connections"][port])
        ]
        if len(drivers) != 1:
            raise ValueError("Expected one whole symbolic driver: " + name)
        cell_name, cell, port = drivers[0]
        if cell["type"] not in ("$anyconst", "$anyseq") or cell["connections"][port] != bits:
            raise ValueError("Binding is not a whole free symbolic output: " + name)
        removed.add(cell_name)
        replacements.update({bit: str(value >> index & 1) for index, bit in enumerate(bits)})
    module["cells"] = {n: cell for n, cell in module["cells"].items() if n not in removed}
    for group in (module["ports"], module["netnames"]):
        for item in group.values():
            item["bits"] = [replacements.get(bit, bit) for bit in item["bits"]]
    for cell in module["cells"].values():
        cell["connections"] = {
            port: [replacements.get(bit, bit) for bit in bits]
            for port, bits in cell["connections"].items()
        }
    return result


def check_constants(graph, top, bindings):
    for name, value in bindings.items():
        bits = graph["modules"][top]["netnames"][name]["bits"]
        if bits != [str(value >> index & 1) for index in range(len(bits))]:
            raise ValueError("Changed or undefined specialized stimulus: " + name)
