// Sweep a single NMI edge across IRQ entry. The NMI must never disappear,
// including when it arrives after the IRQ vector has already been selected.
#include "VBbcCpu.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>

int cpu_interrupt_test() {
    for (unsigned delay = 0; delay < 16; delay++) {
        std::array<uint8_t, 65536> ram{};
        const uint8_t program[] = {0xa2, 0xff, 0x9a, 0xa9, 0x77, 0x58,
                                   0xea, 0xea, 0x4c, 0x06, 0x04};
        for (unsigned i = 0; i < sizeof(program); i++)
            ram[0x400 + i] = program[i];
        const uint8_t irq[] = {0xee, 0x00, 0x02, 0x40}, nmi[] = {0xee, 0x01, 0x02, 0x40};
        for (unsigned i = 0; i < 4; i++) {
            ram[0x3000 + i] = irq[i];
            ram[0x4000 + i] = nmi[i];
        }
        ram[0xfffc] = 0;
        ram[0xfffd] = 4;
        ram[0xfffe] = 0;
        ram[0xffff] = 0x30;
        ram[0xfffa] = 0;
        ram[0xfffb] = 0x40;
        VBbcCpu dut;
        dut.enable = 1;
        dut.irq = 0;
        dut.nmi = 0;
        dut.read_data = 0;
        bool triggered = false;
        uint64_t triggerCycle = 0;
        for (uint64_t cycle = 0; cycle < 1200; cycle++) {
            dut.reset = cycle < 8;
            dut.clk = 0;
            dut.eval();
            const uint16_t pc = uint16_t(dut.pc - 1);
            if (!triggered && dut.instruction && pc == 0x407) {
                dut.irq = 1;
                triggered = true;
                triggerCycle = cycle;
            }
            if (triggered && dut.instruction && (pc == 0x3000 || pc == 0x4000))
                dut.irq = 0;
            dut.nmi = triggered && cycle == triggerCycle + delay;
            dut.eval();
            uint8_t data = ram[dut.address];
            if (!dut.reset && dut.write_enable)
                ram[dut.address] = dut.write_data;
            dut.clk = 1;
            dut.eval();
            dut.read_data = data;
            dut.eval();
        }
        if (!triggered || ram[0x201] != 1 || ram[0x200] > 1 || dut.accumulator != 0x77 ||
            dut.stack_pointer != 0xff) {
            std::cerr << "IRQ/NMI collision failed at delay " << delay
                      << ": IRQ=" << unsigned(ram[0x200]) << " NMI=" << unsigned(ram[0x201])
                      << " A=" << unsigned(dut.accumulator) << " S=" << unsigned(dut.stack_pointer)
                      << "\n";
            return 1;
        }
    }
    std::cout << "All 16 IRQ/NMI edge placements preserve one NMI and CPU context\n";
    return 0;
}

// RESET makes three stack reads, preserves A/X/Y, sets I and decrements S.
// Exercise cold entry and a later RESET after TXS, at both clock enables.
int cpu_reset_test(int argc, char **argv) {
    if (argc != 2)
        throw std::runtime_error("Expected independent reset bus fixture");
    std::ifstream fixture(argv[1]);
    std::array<std::array<unsigned, 7>, 2> expected;
    for (unsigned role = 0; role < 2; role++) {
        unsigned kind;
        if (!(fixture >> kind) || kind != role)
            throw std::runtime_error("Missing or malformed reset bus fixture");
        for (auto &address : expected[role])
            if (!(fixture >> address) || address > 65535)
                throw std::runtime_error("Malformed reset bus request");
    }
    std::string extra;
    if (fixture >> extra)
        throw std::runtime_error("Extra reset bus fixture");
    for (unsigned period : {1u, 16u}) {
        std::array<uint8_t, 65536> ram{};
        ram.fill(0xea);
        for (unsigned a = 0x100; a < 0x200; a++)
            ram[a] = 0xaa;
        const uint8_t code[] = {0xa2, 0xff, 0x9a, 0xa9, 0x77, 0x4c, 0x05, 0x04};
        for (unsigned i = 0; i < sizeof(code); i++)
            ram[0x400 + i] = code[i];
        ram[0xfffc] = 0;
        ram[0xfffd] = 4;
        VBbcCpu dut;
        dut.irq = 0;
        dut.nmi = 0;
        dut.read_data = 0;
        bool cold = false, warm = false;
        unsigned trigger = 0, vectors = 0;
        bool previousReset = true, resetBus = false;
        unsigned resetIndex = 0;
        for (unsigned cycle = 0; cycle < 1000 * period; cycle++) {
            dut.enable = cycle % period == period - 1;
            dut.reset =
                cycle < 8 * period || (trigger && cycle >= trigger && cycle < trigger + 8 * period);
            if (previousReset && !dut.reset) {
                resetBus = true;
                resetIndex = 0;
            }
            previousReset = dut.reset;
            dut.clk = 0;
            dut.eval();
            const unsigned pc = uint16_t(dut.pc - 1);
            if (!dut.reset && dut.enable) {
                if (resetBus) {
                    const unsigned address =
                        resetIndex < 7 ? expected[trigger ? 1 : 0][resetIndex] : 0x400;
                    if (dut.address != address || dut.write_enable) {
                        std::cerr << "RESET bus differs at slot " << resetIndex
                                  << " period=" << period << ": expected " << address << ", actual "
                                  << dut.address << "\n";
                        return 1;
                    }
                    if (++resetIndex == 8)
                        resetBus = false;
                }
                if (dut.write_enable) {
                    std::cerr << "RESET fixture wrote $" << std::hex << dut.address
                              << " period=" << std::dec << period << "\n";
                    return 1;
                }
                if (dut.address == 0xfffc || dut.address == 0xfffd)
                    vectors++;
                if (dut.instruction && pc == 0x400) {
                    if (!cold) {
                        cold = true;
                        if (dut.stack_pointer != 0xfd || !(dut.flags & 4))
                            return 1;
                    } else if (trigger) {
                        warm = true;
                        if (dut.stack_pointer != 0xfc || dut.accumulator != 0x77 ||
                            dut.index_x != 0xff || !(dut.flags & 4)) {
                            std::cerr << "RESET context differs at enable period " << period
                                      << "\n";
                            return 1;
                        }
                        break;
                    }
                }
                if (cold && !trigger && dut.instruction && pc == 0x405)
                    trigger = cycle + 1;
            }
            const uint8_t data = ram[dut.address];
            const bool enable = dut.enable;
            dut.clk = 1;
            dut.eval();
            if (enable)
                dut.read_data = data;
            dut.eval();
        }
        if (!cold || !warm || vectors != 4) {
            std::cerr << "Incomplete RESET fixture\n";
            return 1;
        }
    }
    std::cout << "Cold/warm RESET: exact seven-slot bus sequence, preserved memory/context at "
                 "enable periods 1 and 16\n";
    return 0;
}
