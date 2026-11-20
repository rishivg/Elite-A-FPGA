// Published NMOS reset behavior on the independent cycle core: stack reads,
// three S decrements, preserved general registers and exactly one vector.
#define CHIPS_IMPL
#include "m6502.h"
#include <array>
#include <iostream>
int main() {
    std::array<uint8_t, 65536> ram{};
    ram.fill(0xea);
    const uint8_t program[] = {0xa2, 0xff, 0x9a, 0xa9, 0x77, 0x4c, 0x05, 0x04};
    for (unsigned i = 0; i < sizeof(program); i++)
        ram[0x400 + i] = program[i];
    ram[0xfffc] = 0;
    ram[0xfffd] = 4;
    m6502_t cpu;
    m6502_desc_t desc{};
    uint64_t pins = m6502_init(&cpu, &desc);
    bool cold = false, warm = false, triggered = false;
    unsigned vectors = 0;
    for (unsigned cycle = 0; cycle < 250; cycle++) {
        unsigned address = M6502_GET_ADDR(pins);
        if (pins & M6502_SYNC) {
            if (address == 0x400) {
                if (!cold) {
                    cold = true;
                    if (cpu.S != 0xfd || !(cpu.P & M6502_IF))
                        return 1;
                } else if (triggered) {
                    warm = true;
                    if (cpu.S != 0xfc || cpu.A != 0x77 || cpu.X != 0xff || !(cpu.P & M6502_IF))
                        return 1;
                    break;
                }
            }
            if (cold && !triggered && address == 0x405) {
                triggered = true;
                pins |= M6502_RES;
            }
        }
        pins = m6502_tick(&cpu, pins);
        address = M6502_GET_ADDR(pins);
        if (!(pins & M6502_RW)) {
            std::cerr << "RESET fixture wrote memory\n";
            return 1;
        }
        if (address == 0xfffc || address == 0xfffd)
            vectors++;
        M6502_SET_DATA(pins, ram[address]);
    }
    if (!cold || !warm || vectors != 4)
        return 1;
    std::cout << "Independent cold/warm RESET: reads only, preserved A/X, S minus 3, I set\n";
    return 0;
}
