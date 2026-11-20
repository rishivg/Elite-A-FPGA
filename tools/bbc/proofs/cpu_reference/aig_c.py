"""Evaluate a port-labelled combinational AIG in C for independent comparison."""

import re

# Match the native C fields explicitly rather than using the translator's
# expression or type evaluator. Bits below refer to its published wire format.
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


def parse(text):
    lines = text.splitlines()
    header = lines[0].split()
    if len(header) != 6 or header[0] != "aag":
        raise ValueError("Expected plain ASCII AIG without property extensions")
    maximum, inputs, latches, outputs, count = map(int, header[1:])
    if latches or inputs != 320 or outputs != 321 or maximum != inputs + count:
        raise ValueError("Expected the complete 320-input, 321-output combinational source model")
    incoming = list(map(int, lines[1 : inputs + 1]))
    result = list(map(int, lines[inputs + 1 : inputs + 1 + outputs]))
    gates = [
        tuple(map(int, row.split()))
        for row in lines[inputs + 1 + outputs : inputs + 1 + outputs + count]
    ]
    if incoming != [2 * n for n in range(1, inputs + 1)]:
        raise ValueError("Noncanonical AIG input order")
    defined = {0, *range(1, inputs + 1)}
    for expected, gate in enumerate(gates, inputs + 1):
        if (
            len(gate) != 3
            or gate[0] != 2 * expected
            or any(n < 0 or n // 2 not in defined for n in gate[1:])
        ):
            raise ValueError("Malformed or nontopological AIG gate")
        defined.add(expected)
    if any(n < 0 or n // 2 not in defined for n in result):
        raise ValueError("Undefined AIG output")
    labels = {"i": {}, "o": {}}
    for row in lines[inputs + 1 + outputs + count :]:
        if row == "c":
            break
        match = re.fullmatch(r"([io])(\d+) (.+)", row)
        if not match:
            raise ValueError("Unsupported AIG symbol")
        direction, index, names = match[1], int(match[2]), match[3].split()
        if index in labels[direction]:
            raise ValueError("Duplicate AIG symbol")
        labels[direction][index] = names
    ports = {
        "i": {"state_in": 256, "pins_in": 64},
        "o": {"state_out": 256, "pins_out": 64, "trap": 1},
    }
    bindings = {"i": {}, "o": {}}
    for direction in ("i", "o"):
        for ordinal, names in labels[direction].items():
            limit = inputs if direction == "i" else outputs
            if ordinal >= limit:
                raise ValueError("Out-of-range AIG symbol")
            matches = []
            for name in names:
                match = re.fullmatch(r"(\w+)(?:\[(\d+)\])?", name)
                if match and match[1] in ports[direction]:
                    port, offset = match[1], int(match[2] or 0)
                    if offset >= ports[direction][port]:
                        raise ValueError("Out-of-range AIG port bit")
                    matches.append((port, offset))
            if len(matches) != 1 or matches[0] in bindings[direction]:
                raise ValueError("Missing or aliased published port binding")
            bindings[direction][matches[0]] = ordinal
        expected = {(p, b) for p, width in ports[direction].items() for b in range(width)}
        if set(bindings[direction]) != expected:
            raise ValueError("Incomplete published AIG port coverage")
    return maximum, gates, result, bindings


def literal(value):
    if value <= 1:
        return str(value)
    return f"!node_{value // 2}" if value & 1 else f"node_{value // 2}"


def generate(text):
    maximum, gates, outputs, ports = parse(text)
    field_bits = {}
    offset = 256
    for name, width in FIELDS:
        offset -= width
        field_bits.update({offset + b: (name, b) for b in range(width)})
    if offset != 0:
        raise ValueError("Changed published C state wire format")
    code = [
        "#include <stdint.h>",
        "#include <stdbool.h>",
        "struct aig_result { uint64_t fields[18]; uint64_t pins; bool trap; };",
        "static struct aig_result aig_step(const m6502_t *input, uint64_t incoming) {",
        "bool node_0=false;",
        "struct aig_result result={0};",
    ]
    for (port, bit), ordinal in sorted(ports["i"].items()):
        if port == "pins_in":
            word, shift = "incoming", bit
        else:
            name, shift = field_bits[bit]
            word = "((uint64_t)input->" + name + ")"
        code.append(f"bool node_{ordinal + 1}=({word} >> {shift}) & UINT64_C(1);")
    code.extend(f"bool node_{lhs // 2}=({literal(a)}) && ({literal(b)});" for lhs, a, b in gates)
    offset = 256
    for index, (_, width) in enumerate(FIELDS):
        offset -= width
        for bit in range(width):
            expression = literal(outputs[ports["o"][("state_out", offset + bit)]])
            code.append(f"result.fields[{index}] |= ((uint64_t)({expression})) << {bit};")
    for bit in range(64):
        expression = literal(outputs[ports["o"][("pins_out", bit)]])
        code.append(f"result.pins |= ((uint64_t)({expression})) << {bit};")
    code += [f"result.trap={literal(outputs[ports['o'][('trap', 0)]])};", "return result;", "}"]
    return "\n".join(code) + "\n"


def microcodes(source):
    """Recognize admission directly from the maintained header, not its translator."""
    cases = re.findall(r"case \(0x([0-9A-Fa-f]{2})<<3\)\|([0-7]): ([^\n]+)", source)
    labels = [(int(op, 16) * 8 + int(step), body) for op, step, body in cases]
    if len(labels) != 2048 or {n for n, _ in labels} != set(range(2048)):
        raise ValueError("Changed complete native C case inventory")
    return sorted(n for n, body in labels if not body.startswith("assert(false);"))


def harness(source):
    allowed = microcodes(source)
    masks = [sum(1 << (n & 7) for n in allowed if n >> 3 == op) for op in range(256)]
    fields = "\n".join(f"input.{n}=nondet_word();" for n, _ in FIELDS)
    checks = "\n".join(
        f'CHECK(model.fields[{i}]==(uint64_t)input.{n},"state_{n}");'
        for i, (n, _) in enumerate(FIELDS)
    )
    return f"""#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#define CHIPS_IMPL
#include "m6502.h"
#include "aig_eval.c"
static const unsigned char micro_masks[256]={{{",".join(map(str, masks))}}};
#ifdef NATIVE
static uint64_t seed=UINT64_C(0xe117ea5a9464b451);
static uint64_t nondet_word(void){{seed^=seed<<13;seed^=seed>>7;seed^=seed<<17;return seed;}}
#define CHECK(condition,name) do{{if(!(condition)){{fprintf(stderr,"Failed %s\\n",name);exit(1);}}}}while(0)
#else
extern uint64_t nondet_word(void);
#define CHECK(condition,name) __CPROVER_assert(condition,name)
#endif
static void verify_step(m6502_t input,uint64_t pins){{
 struct aig_result model=aig_step(&input,pins);
 uint64_t actual=m6502_tick(&input,pins);
 CHECK(!model.trap,"trap");
 CHECK(model.pins==actual,"returned_pins");
 {checks}
}}
int main(void){{
#ifdef NATIVE
 static const unsigned valid[{len(allowed)}]={{{",".join(map(str, allowed))}}};
 for(unsigned i=0;i<100000;i++){{
#endif
 m6502_t input={{0}};
 {fields}
 uint64_t pins=nondet_word();
#ifdef NATIVE
 input.IR=valid[i%{len(allowed)}];
 input.bcd_enabled=(nondet_word()&1)!=0;
#else
 bool valid=(input.IR<2048 && ((micro_masks[input.IR>>3]>>(input.IR&7))&1));
 __CPROVER_assume(valid || (pins&M6502_SYNC) || ((pins&(M6502_RW|M6502_RDY))==(M6502_RW|M6502_RDY)));
#endif
 verify_step(input,pins);
#ifdef NATIVE
 }}
 puts("100000 independent native C/AIG transitions: PASS");
#endif
 return 0;
}}
"""
