// Shared instruction inputs only; each CPU executes its own reset and prefix.
#pragma once
#include <array>
#include <cstdint>

#ifdef BBC_LOGIC_DOMAIN
constexpr unsigned BbcArithmeticOperations = 4, BbcArithmeticFlags = 16;
constexpr unsigned BbcArithmeticEnd = 0x308;
constexpr std::array<const char *, 4> BbcArithmeticNames{"ORA", "AND", "EOR", "CMP"};
constexpr unsigned BbcArithmeticCases = 4 * 16 * 256 * 256;
constexpr std::array<unsigned char, 12> BbcArithmeticHeader{'B', 'B', 'C', 'L', 'O', 'G',
                                                            1,   0,   0,   0,   64,  0};
#elif defined(BBC_DECIMAL_TRANSITIONS)
constexpr unsigned BbcArithmeticOperations = 2, BbcArithmeticFlags = 8;
constexpr unsigned BbcArithmeticEnd = 0x31c;
constexpr std::array<const char *, 2> BbcArithmeticNames{"ADC", "SBC"};
constexpr unsigned BbcArithmeticCases = 2 * 8 * 256 * 256;
constexpr std::array<unsigned char, 12> BbcArithmeticHeader{'B', 'B', 'C', 'D', 'E', 'C',
                                                            1,   0,   0,   0,   16,  0};
#else
constexpr unsigned BbcArithmeticOperations = 2, BbcArithmeticFlags = 4;
constexpr unsigned BbcArithmeticEnd = 0x307;
constexpr std::array<const char *, 2> BbcArithmeticNames{"ADC", "SBC"};
constexpr unsigned BbcArithmeticCases = 2 * 4 * 256 * 256;
constexpr std::array<unsigned char, 12> BbcArithmeticHeader{'B', 'B', 'C', 'A', 'L', 'U',
                                                            1,   0,   0,   0,   8,   0};
#endif

inline void arithmetic_program(std::array<uint8_t, 65536> &ram, unsigned operation, unsigned flags,
                               unsigned accumulator, unsigned operand) {
    ram[0xfffc] = 0;
    ram[0xfffd] = 3;
#ifdef BBC_LOGIC_DOMAIN
    // LDA #P; PHA; PLP; LDA #A; OP #B; JMP $0308. All C/D/V/I prefixes
    // execute through the real stack; neither engine's registers are injected.
    const unsigned status =
        0x20 | (flags & 1) | ((flags & 2) ? 8 : 0) | ((flags & 4) ? 64 : 0) | ((flags & 8) ? 4 : 0);
    const unsigned opcodes[] = {0x09, 0x29, 0x49, 0xc9};
    const std::array<uint8_t, 11> program{0xa9,
                                          uint8_t(status),
                                          0x48,
                                          0x28,
                                          0xa9,
                                          uint8_t(accumulator),
                                          uint8_t(opcodes[operation]),
                                          uint8_t(operand),
                                          0x4c,
                                          0x08,
                                          0x03};
#elif defined(BBC_DECIMAL_TRANSITIONS)
    // SED/CLD and both PLP D transitions immediately precede arithmetic.
    // Establish the previous D through ordinary stack instructions, then
    // stage the final P before loading A. Every accumulator/operand and C
    // combination executes normally; I remains set for this arithmetic test.
    const unsigned transition = flags >> 1;
    const bool oldDecimal = transition == 1 || transition == 3;
    const bool newDecimal = !oldDecimal;
    const uint8_t oldStatus = 0x24 | (flags & 1) | (oldDecimal ? 8 : 0);
    const uint8_t newStatus = 0x24 | (flags & 1) | (newDecimal ? 8 : 0);
    const std::array<uint8_t, 14> prefix{0x78,
                                         0xa2,
                                         0xff,
                                         0x9a,
                                         0xa9,
                                         oldStatus,
                                         0x48,
                                         0x28,
                                         0xa9,
                                         newStatus,
                                         0x48,
                                         0xa9,
                                         uint8_t(accumulator),
                                         uint8_t(flags & 1 ? 0x38 : 0x18)};
    for (unsigned i = 0; i < 31; i++)
        ram[0x300 + i] = 0xea;
    for (unsigned i = 0; i < prefix.size(); i++)
        ram[0x300 + i] = prefix[i];
    ram[0x319] = transition == 0 ? 0xf8 : transition == 1 ? 0xd8 : 0x28;
    ram[0x31a] = operation ? 0xe9 : 0x69;
    ram[0x31b] = operand;
    ram[0x31c] = 0x4c;
    ram[0x31d] = 0x1c;
    ram[0x31e] = 0x03;
#else
    // SEI; CLD/SED; CLC/SEC; LDA #A; ADC/SBC #B; JMP $0307.
    const std::array<uint8_t, 10> program{0x78,
                                          uint8_t(flags & 2 ? 0xf8 : 0xd8),
                                          uint8_t(flags & 1 ? 0x38 : 0x18),
                                          0xa9,
                                          uint8_t(accumulator),
                                          uint8_t(operation ? 0xe9 : 0x69),
                                          uint8_t(operand),
                                          0x4c,
                                          0x07,
                                          0x03};
#endif
#ifndef BBC_DECIMAL_TRANSITIONS
    for (unsigned i = 0; i < program.size(); ++i)
        ram[0x300 + i] = program[i];
#endif
}
