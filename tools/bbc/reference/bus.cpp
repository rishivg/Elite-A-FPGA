// Independent pinned NMOS core generates complete bus requests; no DUT inputs.
#define CHIPS_IMPL
#include "m6502.h"
#include <array>
#include <iostream>
#include <vector>
int main() {

    const unsigned groups[6][4] = {{6, 22, 14, 30},      {38, 54, 46, 62},
                                   {70, 86, 78, 94},     {102, 118, 110, 126},
                                   {198, 214, 206, 222}, {230, 246, 238, 254}};
    std::vector<std::array<unsigned, 4>> cases;
    for (auto &group : groups)
        for (unsigned mode = 0; mode < 5; mode++)
            for (unsigned value : {0u, 1u, 127u, 128u, 255u})
                for (unsigned carry = 0; carry < 2; carry++)
                    cases.push_back({group[std::min(mode, 3u)], mode, value, carry});
    for (unsigned opcode : {1u, 33u, 65u, 97u, 129u, 161u, 193u, 225u})
        for (unsigned value : {0u, 128u, 255u})
            for (unsigned carry = 0; carry < 2; carry++)
                cases.push_back({opcode, 5, value, carry});
    for (auto test : cases) {
        unsigned opcode = test[0], mode = test[1], value = test[2], carry = test[3];
        unsigned operand = mode < 2 || mode == 5 ? 0x20 : mode == 4 ? 0x20ff : 0x2000;
        unsigned target =
            mode == 5 ? 0x2000 : operand + (mode == 1 || mode == 3 || mode == 4 ? 1 : 0);
        std::vector<unsigned> code{0xa2,         1, 0xea, 0xea, 0xea, carry ? 0x38u : 0x18u, opcode,
                                   operand & 255};
        if (mode >= 2 && mode != 5)
            code.push_back(operand >> 8);
        code.insert(code.end(), {0x4c, 0, 3});
        std::array<uint8_t, 65536> ram{};
        ram.fill(0xea);
        for (unsigned i = 0; i < code.size(); i++)
            ram[0x400 + i] = code[i];
        ram[target] = value;
        if (mode == 5) {
            ram[0x21] = 0;
            ram[0x22] = 0x20;
        }
        ram[0xfffc] = 0;
        ram[0xfffd] = 4;
        m6502_t cpu;
        m6502_desc_t desc{};
        uint64_t pins = m6502_init(&cpu, &desc);
        int baseline = -1;
        std::vector<std::array<unsigned, 3>> bus;
        bool completed = false;
        for (unsigned cycle = 0; cycle < 200; cycle++) {
            unsigned address = M6502_GET_ADDR(pins);
            if ((pins & M6502_SYNC) && address == 0x406 && baseline < 0)
                baseline = int(cycle);
            if (baseline >= 0 && (pins & M6502_SYNC) && address == 0x406 + code.size() - 9) {
                std::cout << opcode << " " << mode << " " << value << " " << carry << " "
                          << int(cycle) - baseline << " " << unsigned(cpu.A) << " "
                          << unsigned(cpu.X) << " " << unsigned(cpu.Y) << " " << unsigned(cpu.S)
                          << " " << (cpu.P & 0xc3);
                for (auto b : bus)
                    std::cout << " " << b[0] << ":" << b[1] << ":" << b[2];
                std::cout << "\n";
                completed = true;
                break;
            }
            if (baseline >= 0)
                bus.push_back({address, unsigned(!(pins & M6502_RW)),
                               unsigned(pins & M6502_RW ? ram[address] : M6502_GET_DATA(pins))});
            if (pins & M6502_RW) {
                M6502_SET_DATA(pins, ram[address]);
            } else {
                ram[address] = M6502_GET_DATA(pins);
            }
            pins = m6502_tick(&cpu, pins);
        }
        if (!completed)
            return 1;
    }
    return 0;
}
