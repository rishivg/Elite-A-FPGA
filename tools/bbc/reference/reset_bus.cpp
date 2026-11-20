// Declared reset-entry contexts: cold PC/S=0, warm PC=$0407/S=$FF/A=$77/X=$FF.
// Independent NMOS bus requests; no DUT register or memory inputs.
#define CHIPS_IMPL
#include "m6502.h"
#include <array>
#include <iostream>
int main() {
    for (unsigned warm = 0; warm < 2; warm++) {
        m6502_t cpu;
        m6502_desc_t desc{};
        uint64_t pins = m6502_init(&cpu, &desc);
        const unsigned pc = warm ? 0x407 : 0;
        if (warm) {
            m6502_set_pc(&cpu, pc);
            m6502_set_s(&cpu, 0xff);
            m6502_set_a(&cpu, 0x77);
            m6502_set_x(&cpu, 0xff);
        }
        M6502_SET_ADDR(pins, pc);
        std::array<uint8_t, 65536> ram{};
        ram.fill(0xea);
        ram[0xfffc] = 0;
        ram[0xfffd] = 4;
        std::cout << warm;
        for (unsigned i = 0; i < 7; i++) {
            if (!(pins & M6502_RW))
                return 1;
            std::cout << " " << M6502_GET_ADDR(pins);
            M6502_SET_DATA(pins, ram[M6502_GET_ADDR(pins)]);
            pins = m6502_tick(&cpu, pins);
        }
        if (!(pins & M6502_SYNC) || M6502_GET_ADDR(pins) != 0x400)
            return 1;
        std::cout << "\n";
    }
    return 0;
}
