#define CHIPS_IMPL
#include "m6502.h"
#include <array>
#include <fstream>
#include <iostream>
int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    std::array<uint8_t, 65536> ram{};
    std::ifstream input(argv[1], std::ios::binary);
    input.read(reinterpret_cast<char *>(ram.data()), ram.size());
    if (input.gcount() != 65536)
        return 2;
    ram[0xfffc] = 0;
    ram[0xfffd] = 4;
    m6502_t cpu;
    m6502_desc_t desc{};
    uint64_t pins = m6502_init(&cpu, &desc);
    unsigned previous = 0, repeats = 0;
    for (uint64_t cycle = 8; cycle < 150000000; cycle++) {
        pins = m6502_tick(&cpu, pins);
        const unsigned address = M6502_GET_ADDR(pins);
        if (pins & M6502_RW) {
            M6502_SET_DATA(pins, ram[address]);
        } else {
            ram[address] = M6502_GET_DATA(pins);
        }
        if (pins & M6502_SYNC) {
            if (address == previous)
                repeats++;
            else {
                previous = address;
                repeats = 0;
            }
            if (repeats >= 10) {
                std::cout << "Trap=" << std::hex << address << std::dec << " cycle=" << cycle
                          << "\n";
                return address == 0x3469 ? 0 : 1;
            }
        }
    }
    return 1;
}
