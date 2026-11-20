#define CHIPS_IMPL
#include "interrupt_stimulus.h"
#include "m6502.h"
#include <array>
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3)
        return 2;
    const std::string signal = argc == 3 ? argv[2] : "irq";
    if (signal != "irq" && signal != "nmi")
        return 2;
    const unsigned vector = signal == "nmi" ? 0xfffa : 0xfffe;
    const uint64_t interruptPin = signal == "nmi" ? M6502_NMI : M6502_IRQ;
    std::ifstream fixtures(argv[1]);
    if (!fixtures)
        return 2;

    unsigned kind, delay, width, expectedPc, expectedFlags, count = 0, failures = 0;
    int expectedCycles;
    while (fixtures >> kind >> delay >> width >> expectedPc >> expectedCycles >> expectedFlags) {
        if (kind >= 14 || delay >= 16 || width > 2)
            return 2;
        unsigned base;
        auto ram = interrupt_ram(kind, vector, base);
        m6502_t cpu;
        m6502_desc_t desc{};
        uint64_t pins = m6502_init(&cpu, &desc);
        int baseline = -1, actualCycles = -1;
        unsigned actualPc = 0, actualFlags = 0;
        for (int cycle = 0; cycle < 250; cycle++) {
            if (cycle < 8)
                pins |= M6502_RES;
            else
                pins &= ~M6502_RES;
            const unsigned pc = M6502_GET_ADDR(pins);
            if (cycle >= 8 && (pins & M6502_SYNC) && pc == base && baseline < 0) {
                baseline = cycle;
                if (kind == 9) {
                    ram[0x1fd] = 0x20;
                    ram[0x1fe] = 0x20;
                    ram[0x1ff] = 4;
                }
            }
            const int elapsed = cycle - baseline;
            if (baseline >= 0 && elapsed >= int(delay) && (!width || elapsed < int(delay + width)))
                pins |= interruptPin;
            else
                pins &= ~interruptPin;
            if (baseline >= 0 && (pins & M6502_SYNC) && pc == 0x3000) {
                actualPc = (ram[0x1ff] << 8) | ram[0x1fe];
                actualCycles = elapsed;
                actualFlags = ram[0x1fd];
                break;
            }
            pins = m6502_tick(&cpu, pins);
            unsigned address = M6502_GET_ADDR(pins);
            if (pins & M6502_RW) {
                M6502_SET_DATA(pins, ram[address]);
            } else if (cycle >= 8)
                ram[address] = M6502_GET_DATA(pins);
        }
        count++;
        if (actualPc != expectedPc || actualCycles != expectedCycles ||
            actualFlags != expectedFlags) {
            failures++;
            if (failures <= 12)
                std::cerr << "case " << kind << "/" << delay << "/" << width << ": expected "
                          << expectedPc << "/" << expectedCycles << "/" << expectedFlags
                          << ", actual " << actualPc << "/" << actualCycles << "/" << actualFlags
                          << "\n";
        }
    }
    std::cout << count - failures << "/" << count << " NMOS " << signal
              << " timing fixtures passed\n";
    return failures || count != 672 || !fixtures.eof() ? 1 : 0;
}
