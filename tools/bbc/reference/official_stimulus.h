// Declared component INPUTS shared by independent CPU tests, never DUT results.
// The opcode/addressing facts come from the pinned original NMOS descriptions.
// Each engine executes the prefix normally; no register/context state is copied.
#pragma once
#include <array>
#include <vector>
enum OfficialAddressMode : unsigned {
    Implied,
    Immediate,
    ZeroPage,
    ZeroPageX,
    ZeroPageY,
    Absolute,
    AbsoluteX,
    AbsoluteY,
    IndexedIndirect,
    IndirectIndexed,
    Jump,
    IndirectJump,
    Call,
    Return,
    ReturnInterrupt,
    Break,
    Relative
};
constexpr unsigned OfficialCaseCount = 9920;
constexpr std::array<std::array<unsigned, 2>, 151> OfficialOpcodes{{
    {0x00, Break},           // BRK
    {0x01, IndexedIndirect}, // ORA (zp,X)
    {0x05, ZeroPage},        // ORA zp
    {0x06, ZeroPage},        // ASL zp
    {0x08, Implied},         // PHP
    {0x09, Immediate},       // ORA #
    {0x0a, Implied},         // ASLA
    {0x0d, Absolute},        // ORA abs
    {0x0e, Absolute},        // ASL abs
    {0x10, Relative},        // BPL #
    {0x11, IndirectIndexed}, // ORA (zp),Y
    {0x15, ZeroPageX},       // ORA zp,X
    {0x16, ZeroPageX},       // ASL zp,X
    {0x18, Implied},         // CLC
    {0x19, AbsoluteY},       // ORA abs,Y
    {0x1d, AbsoluteX},       // ORA abs,X
    {0x1e, AbsoluteX},       // ASL abs,X
    {0x20, Call},            // JSR
    {0x21, IndexedIndirect}, // AND (zp,X)
    {0x24, ZeroPage},        // BIT zp
    {0x25, ZeroPage},        // AND zp
    {0x26, ZeroPage},        // ROL zp
    {0x28, Implied},         // PLP
    {0x29, Immediate},       // AND #
    {0x2a, Implied},         // ROLA
    {0x2c, Absolute},        // BIT abs
    {0x2d, Absolute},        // AND abs
    {0x2e, Absolute},        // ROL abs
    {0x30, Relative},        // BMI #
    {0x31, IndirectIndexed}, // AND (zp),Y
    {0x35, ZeroPageX},       // AND zp,X
    {0x36, ZeroPageX},       // ROL zp,X
    {0x38, Implied},         // SEC
    {0x39, AbsoluteY},       // AND abs,Y
    {0x3d, AbsoluteX},       // AND abs,X
    {0x3e, AbsoluteX},       // ROL abs,X
    {0x40, ReturnInterrupt}, // RTI
    {0x41, IndexedIndirect}, // EOR (zp,X)
    {0x45, ZeroPage},        // EOR zp
    {0x46, ZeroPage},        // LSR zp
    {0x48, Implied},         // PHA
    {0x49, Immediate},       // EOR #
    {0x4a, Implied},         // LSRA
    {0x4c, Jump},            // JMP
    {0x4d, Absolute},        // EOR abs
    {0x4e, Absolute},        // LSR abs
    {0x50, Relative},        // BVC #
    {0x51, IndirectIndexed}, // EOR (zp),Y
    {0x55, ZeroPageX},       // EOR zp,X
    {0x56, ZeroPageX},       // LSR zp,X
    {0x58, Implied},         // CLI
    {0x59, AbsoluteY},       // EOR abs,Y
    {0x5d, AbsoluteX},       // EOR abs,X
    {0x5e, AbsoluteX},       // LSR abs,X
    {0x60, Return},          // RTS
    {0x61, IndexedIndirect}, // ADC (zp,X)
    {0x65, ZeroPage},        // ADC zp
    {0x66, ZeroPage},        // ROR zp
    {0x68, Implied},         // PLA
    {0x69, Immediate},       // ADC #
    {0x6a, Implied},         // RORA
    {0x6c, IndirectJump},    // JMPI
    {0x6d, Absolute},        // ADC abs
    {0x6e, Absolute},        // ROR abs
    {0x70, Relative},        // BVS #
    {0x71, IndirectIndexed}, // ADC (zp),Y
    {0x75, ZeroPageX},       // ADC zp,X
    {0x76, ZeroPageX},       // ROR zp,X
    {0x78, Implied},         // SEI
    {0x79, AbsoluteY},       // ADC abs,Y
    {0x7d, AbsoluteX},       // ADC abs,X
    {0x7e, AbsoluteX},       // ROR abs,X
    {0x81, IndexedIndirect}, // STA (zp,X)
    {0x84, ZeroPage},        // STY zp
    {0x85, ZeroPage},        // STA zp
    {0x86, ZeroPage},        // STX zp
    {0x88, Implied},         // DEY
    {0x8a, Implied},         // TXA
    {0x8c, Absolute},        // STY abs
    {0x8d, Absolute},        // STA abs
    {0x8e, Absolute},        // STX abs
    {0x90, Relative},        // BCC #
    {0x91, IndirectIndexed}, // STA (zp),Y
    {0x94, ZeroPageX},       // STY zp,X
    {0x95, ZeroPageX},       // STA zp,X
    {0x96, ZeroPageY},       // STX zp,Y
    {0x98, Implied},         // TYA
    {0x99, AbsoluteY},       // STA abs,Y
    {0x9a, Implied},         // TXS
    {0x9d, AbsoluteX},       // STA abs,X
    {0xa0, Immediate},       // LDY #
    {0xa1, IndexedIndirect}, // LDA (zp,X)
    {0xa2, Immediate},       // LDX #
    {0xa4, ZeroPage},        // LDY zp
    {0xa5, ZeroPage},        // LDA zp
    {0xa6, ZeroPage},        // LDX zp
    {0xa8, Implied},         // TAY
    {0xa9, Immediate},       // LDA #
    {0xaa, Implied},         // TAX
    {0xac, Absolute},        // LDY abs
    {0xad, Absolute},        // LDA abs
    {0xae, Absolute},        // LDX abs
    {0xb0, Relative},        // BCS #
    {0xb1, IndirectIndexed}, // LDA (zp),Y
    {0xb4, ZeroPageX},       // LDY zp,X
    {0xb5, ZeroPageX},       // LDA zp,X
    {0xb6, ZeroPageY},       // LDX zp,Y
    {0xb8, Implied},         // CLV
    {0xb9, AbsoluteY},       // LDA abs,Y
    {0xba, Implied},         // TSX
    {0xbc, AbsoluteX},       // LDY abs,X
    {0xbd, AbsoluteX},       // LDA abs,X
    {0xbe, AbsoluteY},       // LDX abs,Y
    {0xc0, Immediate},       // CPY #
    {0xc1, IndexedIndirect}, // CMP (zp,X)
    {0xc4, ZeroPage},        // CPY zp
    {0xc5, ZeroPage},        // CMP zp
    {0xc6, ZeroPage},        // DEC zp
    {0xc8, Implied},         // INY
    {0xc9, Immediate},       // CMP #
    {0xca, Implied},         // DEX
    {0xcc, Absolute},        // CPY abs
    {0xcd, Absolute},        // CMP abs
    {0xce, Absolute},        // DEC abs
    {0xd0, Relative},        // BNE #
    {0xd1, IndirectIndexed}, // CMP (zp),Y
    {0xd5, ZeroPageX},       // CMP zp,X
    {0xd6, ZeroPageX},       // DEC zp,X
    {0xd8, Implied},         // CLD
    {0xd9, AbsoluteY},       // CMP abs,Y
    {0xdd, AbsoluteX},       // CMP abs,X
    {0xde, AbsoluteX},       // DEC abs,X
    {0xe0, Immediate},       // CPX #
    {0xe1, IndexedIndirect}, // SBC (zp,X)
    {0xe4, ZeroPage},        // CPX zp
    {0xe5, ZeroPage},        // SBC zp
    {0xe6, ZeroPage},        // INC zp
    {0xe8, Implied},         // INX
    {0xe9, Immediate},       // SBC #
    {0xea, Implied},         // NOP
    {0xec, Absolute},        // CPX abs
    {0xed, Absolute},        // SBC abs
    {0xee, Absolute},        // INC abs
    {0xf0, Relative},        // BEQ #
    {0xf1, IndirectIndexed}, // SBC (zp),Y
    {0xf5, ZeroPageX},       // SBC zp,X
    {0xf6, ZeroPageX},       // INC zp,X
    {0xf8, Implied},         // SED
    {0xf9, AbsoluteY},       // SBC abs,Y
    {0xfd, AbsoluteX},       // SBC abs,X
    {0xfe, AbsoluteX},       // INC abs,X
}};
struct OfficialStimulus {
    std::array<unsigned char, 65536> ram;
    unsigned pc;
    std::vector<std::array<unsigned, 2>> inputs;
};
inline OfficialStimulus official_stimulus(unsigned opcode, unsigned mode, unsigned value,
                                          unsigned flags, unsigned boundary) {
    OfficialStimulus f;
    auto &ram = f.ram;
    ram.fill(0xea);
    auto set = [&](unsigned address, unsigned data) {
        ram[address] = data;
        f.inputs.push_back({address, data});
    };
    const unsigned base = boundary == 2 ? 0x500
                          : boundary    ? (mode == Relative ? 0x4f0 : 0x4f1)
                                        : 0x400;
    // TXS establishes S=FF; PLP declares C/D/V/I, then loads establish A/X/Y/N/Z.
    const std::vector<unsigned> prefix = {
        0xa2, 255, 0x9a, 0xa9, 0x24 | (flags & 0x49), 0x48, 0x28, 0xa2, 1, 0xa0, 2, 0xa9, value};
    f.pc = base + prefix.size();
    for (unsigned i = 0; i < prefix.size(); i++)
        set(base + i, prefix[i]);
    unsigned operand = boundary ? 255 : 0x20, target = operand;
    unsigned absolute = boundary ? 0x20ff : 0x2000;
    if (mode == ZeroPageX)
        target = (operand + 1) & 255;
    if (mode == ZeroPageY)
        target = (operand + 2) & 255;
    if (mode == Absolute || mode == AbsoluteX || mode == AbsoluteY) {
        operand = absolute;
        target = absolute + (mode == AbsoluteX ? 1 : mode == AbsoluteY ? 2 : 0);
    }
    if (mode == IndexedIndirect) {
        unsigned pointer = (operand + 1) & 255;
        set(pointer, 0);
        set((pointer + 1) & 255, 0x20);
        target = 0x2000;
    }
    if (mode == IndirectIndexed) {
        absolute = boundary ? 0x20ff : 0x2000;
        set(operand, absolute & 255);
        set((operand + 1) & 255, absolute >> 8);
        target = absolute + 2;
    }
    if (mode >= ZeroPage && mode <= IndirectIndexed)
        set(target, value);
    if (mode == Immediate)
        operand = value;
    if (mode == Jump || mode == Call)
        operand = 0x3000;
    if (mode == IndirectJump) {
        operand = boundary ? 0x20ff : 0x2000;
        set(operand, 0);
        set((operand & 0xff00) | ((operand + 1) & 255), 0x30);
        set(operand + 1, boundary ? 0x40 : 0x30);
    }
    if (mode == Relative)
        operand = boundary == 2 ? 0xe0 : 5;
    set(f.pc, opcode);
    if ((mode != Implied && mode < Return) || mode == Relative)
        set(f.pc + 1, operand & 255);
    if ((mode >= Absolute && mode <= AbsoluteY) || (mode >= Jump && mode <= Call))
        set(f.pc + 2, operand >> 8);
    if (mode == Return) {
        set(0x100, 0xff);
        set(0x101, 0x2f);
    }
    if (mode == ReturnInterrupt) {
        set(0x100, 0x24 | (flags & 0x49));
        set(0x101, 0);
        set(0x102, 0x30);
    }
    if (opcode == 0x28 || opcode == 0x68)
        set(0x100, value);
    set(0xfffc, base & 255);
    set(0xfffd, base >> 8);
    set(0xfffe, 0);
    set(0xffff, 0x30);
    return f;
}
