// The unchanged independent NMOS engine records BRK bus and flag behavior.
#define CHIPS_IMPL
#include "bbc_cpu_brk_stimulus.h"
#include "m6502.h"
#include <iostream>
#include <vector>
int main() {
    for (unsigned id = 0; id < BrkCaseCount; id++) {
        auto input = brk_stimulus(id);
        m6502_t cpu;
        m6502_desc_t desc{};
        uint64_t pins = m6502_init(&cpu, &desc);
        std::vector<std::array<unsigned, 3>> bus;
        bool started = false, done = false;
        for (unsigned cycle = 0; cycle < 100; cycle++) {
            unsigned address = M6502_GET_ADDR(pins);
            if ((pins & M6502_SYNC) && address == input.target && !started)
                started = true;
            else if (started && (pins & M6502_SYNC)) {
                if (bus.size() != 7)
                    return 1;
                std::cout << id << " " << address << " " << unsigned(cpu.A) << " "
                          << unsigned(cpu.X) << " " << unsigned(cpu.Y) << " " << unsigned(cpu.S)
                          << " " << (cpu.P & 0xcf);
                for (auto row : bus)
                    std::cout << " " << row[0] << ":" << row[1] << ":" << row[2];
                std::cout << "\n";
                done = true;
                break;
            }
            if (started)
                bus.push_back(
                    {address, unsigned(!(pins & M6502_RW)),
                     unsigned(pins & M6502_RW ? input.ram[address] : M6502_GET_DATA(pins))});
            if (pins & M6502_RW) {
                M6502_SET_DATA(pins, input.ram[address]);
            } else
                input.ram[address] = M6502_GET_DATA(pins);
            pins = m6502_tick(&cpu, pins);
        }
        if (!done)
            return 1;
    }
    std::cout << "total " << BrkCaseCount << "\n";
    return !std::cout;
}
