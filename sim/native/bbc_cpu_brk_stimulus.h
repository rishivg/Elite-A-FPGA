// Declared firmware and memory inputs; both CPUs execute their own state.
#pragma once
#include <array>
#include <cstdint>
constexpr unsigned BrkCaseCount = 256 * 64 * 4;
struct BrkStimulus {
    std::array<uint8_t, 65536> ram;
    uint16_t target;
};
inline BrkStimulus brk_stimulus(unsigned id) {
    const unsigned padding = id % 256, flagCase = (id / 256) % 64, context = id / (256 * 64);
    constexpr std::array<uint16_t, 4> start{0x0400, 0x04f2, 0xffe0, 0x7ff2};
    constexpr std::array<uint16_t, 4> vector{0xc200, 0x0200, 0x00ff, 0xa5ff};
    constexpr std::array<uint8_t, 4> stack{0xff, 0x00, 0x80, 0xef};
    const uint8_t flags = 0x30 | (flagCase & 15) | ((flagCase & 16) << 2) | ((flagCase & 32) << 2);
    const uint8_t a = padding * 37 + flagCase * 11 + context * 53;
    const uint8_t x = padding * 73 + flagCase * 19 + context * 31;
    const uint8_t y = padding * 109 + flagCase * 47 + context * 7;
    const std::array<uint8_t, 15> program{
        0xa2, stack[context],  0x9a, 0xa9, flags, 0x48, 0xa9, a, 0xa2, x, 0xa0, y, 0x28,
        0x00, uint8_t(padding)};
    BrkStimulus input;
    input.ram.fill(0xea);
    input.target = start[context] + 13;
    for (unsigned i = 0; i < program.size(); i++)
        input.ram[uint16_t(start[context] + i)] = program[i];
    input.ram[0xfffc] = start[context];
    input.ram[0xfffd] = start[context] >> 8;
    input.ram[0xfffe] = vector[context];
    input.ram[0xffff] = vector[context] >> 8;
    return input;
}
