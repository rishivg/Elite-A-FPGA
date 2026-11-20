"""Derive NMOS stack/control-flow bus order from the pinned enabled core.

PUSH/PULL retain their ALU schedule but expose the real stack dummy/read/write
slots. JSR pushes after its stack dummy read and fetches the high operand last;
JMP1 then forms the target on the synchronous-memory interface. RTS dummy-reads
the unincremented return address. Indirect JMP wraps only its low pointer byte.
JSR's stack address and indirect JMP share the same eight-bit ADD+1 expression.
BRK's padding byte is a dummy read, so it must not select ADC/SBC flag retirement
or decimal arithmetic during vector entry. Every replacement is restricted to
one recognized block of the pinned source.
"""


def correct_bus(source):
    s = source

    def section(start, end, callback):
        nonlocal s
        a = s.index(start)
        b = s.index(end, a)
        part = s[a:b]
        s = s[:a] + callback(part) + s[b:]

    def swap(p, a, b, n=1):
        if p.count(a) != n:
            raise ValueError("Pinned CPU bus decode differs: " + a)
        return p.replace(a, b)

    def pc_temp(p):
        p = swap(p, "        JMPI1,\n", "")
        p = swap(p, "        JSR3,\n", "")
        return swap(
            p,
            "        default:        PC_temp = PC;",
            "        JMPI1:          PC_temp = { DIMUX, ADD + 8'h01 };\n        default:        PC_temp = PC;",
        )

    section(" * Program Counter Increment/Load.", " * Determine wether", pc_temp)

    def pc_inc(p):
        p = swap(
            p,
            "                            PC_inc = 1;",
            "                            PC_inc = ((IR & 8'h9f) == 8'h08) ? 0 : 1;",
        )
        p = swap(p, "        JMPI1,\n", "")
        return swap(p, "        BRK3,\n", "        BRK3,\n        PUSH1,\n        PULL2,\n")

    section(" * Determine wether", " * Set new PC", pc_inc)

    def address(p):
        p = swap(p, "        PUSH1,\n", "        PULL0,\n")
        p = swap(p, "        JSR1,\n", "")
        p = swap(p, "        RTI4,\n", "        RTI4,\n        RTS3,\n")
        return swap(
            p,
            "        default:        AB = PC;",
            "        PUSH0:          AB = { STACKPAGE, AXYS[SEL_S] };\n        JSR1:           AB = { STACKPAGE, ABL };\n        JSR2:           AB = { STACKPAGE, ADD + 8'h01 };\n        DECODE:         AB = PC_temp;\n        default:        AB = PC;",
        )

    section(" * Address Generator", " * ABH/ABL pair", address)

    def data(p):
        p = swap(p, "        JSR0,\n", "        JSR0,\n        JSR1,\n")
        p = swap(p, "        JSR1,\n        BRK1:", "        JSR2,\n        BRK1:")
        return swap(
            p,
            "        PUSH1:   DO = php ? P : ADD;",
            "        PUSH0:   DO = php ? P : AXYS[SEL_A];\n        PUSH1:   DO = php ? P : ADD;",
        )

    section(" * Data Out MUX", " * Write Enable Generator", data)

    def we(p):
        p = swap(p, "        JSR0,\n", "")
        p = swap(p, "        JSR1,\n", "        JSR1,\n        JSR2,\n")
        return swap(p, "        PUSH1,\n", "        PUSH0,\n")

    section(" * Write Enable Generator", " * register file", we)
    section(
        " * ALU A Input MUX",
        " * ALU B Input mux",
        lambda p: swap(p, "        JSR1,\n", "        JSR1,\n        JSR3,\n"),
    )
    section(
        " * ALU B Input mux",
        " * ALU CI",
        lambda p: swap(p, "         JSR2,\n", "         JSR2,\n         JSR3,\n"),
    )
    s = swap(s, "        JSR3    : state <= FETCH;", "        JSR3    : state <= JMP1;")
    # BRK0 sees the discarded padding byte on DI. The upstream decoder also
    # runs here to clear arithmetic controls, but decoding that byte as ADC/SBC
    # corrupts C/V at the first handler instruction and can enable decimal ALU
    # behavior. DECODE still selects arithmetic normally; BRK0 always clears it.
    s = swap(s, "adc_sbc <= 1;", "adc_sbc <= state != BRK0;")
    # The previous SED/CLD/PLP retires D on the same DECODE edge that selects
    # arithmetic for the next opcode. Forward its new value into ADC's BCD
    # control; sampling the old D selects inconsistent carry/adjust behavior.
    s = swap(
        s,
        "adc_bcd <= D;",
        "adc_bcd <= (plp ? ADD[3] : cld ? 1'b0 : sed ? 1'b1 : D) && state != BRK0;",
    )
    return s
