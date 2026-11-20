// Compare IRQ/NMI arrival/pulse timing with independently generated NMOS fixtures.
#include "VBbcCpu.h"
#include "interrupt_stimulus.h"
#include <array>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

int cpu_timing_test(int argc, char **argv) {
    try {
        if (argc < 2 || argc > 4)
            throw std::runtime_error(
                "Expected fixture file and optional irq/nmi signal and enable period");
        const std::string signal = argc >= 3 ? argv[2] : "irq";
        if (signal != "irq" && signal != "nmi")
            throw std::runtime_error("Expected irq/nmi signal");
        const bool nmi = signal == "nmi";
        const unsigned period = argc == 4 ? std::stoul(argv[3]) : 1;
        if (period < 1 || period > 32)
            throw std::runtime_error("Invalid enable period");
        const unsigned vector = nmi ? 0xfffa : 0xfffe;
        std::ifstream fixtures(argv[1]);
        if (!fixtures)
            throw std::runtime_error("Missing fixtures");

        unsigned kind, delay, width, expectedPc, expectedFlags, count = 0, failures = 0;
        int expectedCycles;
        while (fixtures >> kind >> delay >> width >> expectedPc >> expectedCycles >>
               expectedFlags) {
            if (kind >= 14 || delay >= 16 || width > 2)
                throw std::runtime_error("Malformed fixture");
            unsigned base;
            auto ram = interrupt_ram(kind, vector, base);
            // Find the instruction boundary with interrupts inactive. DECODE is
            // one enable after its logical opcode-fetch slot, including stack
            // instructions which prefetch through IRHOLD. Drive the second run at
            // that physical slot; detecting DECODE in the active run is too late.
            const auto initialRam = ram;
            int fetchCycle = -1;
            {
                VBbcCpu probe;
                probe.enable = 1;
                probe.irq = 0;
                probe.nmi = 0;
                probe.read_data = 0;
                for (int cycle = 0; cycle < 250 * int(period); cycle++) {
                    probe.enable = cycle % period == period - 1;
                    probe.reset = cycle < 8 * int(period);
                    probe.clk = 0;
                    probe.eval();
                    if (!probe.reset && probe.enable && probe.instruction &&
                        uint16_t(probe.pc - 1) == base) {
                        fetchCycle = cycle - int(period);
                        break;
                    }
                    const auto data = ram[probe.address];
                    if (!probe.reset && probe.enable && probe.write_enable)
                        ram[probe.address] = probe.write_data;
                    probe.clk = 1;
                    probe.eval();
                    if (probe.enable)
                        probe.read_data = data;
                    probe.eval();
                }
            }
            if (fetchCycle < 0)
                throw std::runtime_error("No initial opcode fetch");
            ram = initialRam;
            VBbcCpu dut;
            dut.enable = 1;
            dut.irq = 0;
            dut.nmi = 0;
            dut.read_data = 0;
            int baseline = fetchCycle + int(period), actualCycles = -1;
            unsigned actualPc = 0, actualFlags = 0;
            for (int cycle = 0; cycle < 250 * int(period); cycle++) {
                dut.enable = cycle % period == period - 1;
                dut.reset = cycle < 8 * int(period);
                dut.clk = 0;
                dut.eval();
                const auto pc = uint16_t(dut.pc - 1);
                if (kind == 9 && cycle == fetchCycle) {
                    ram[0x1fd] = 0x20;
                    ram[0x1fe] = 0x20;
                    ram[0x1ff] = 4;
                }
                const int elapsed = (cycle - baseline) / int(period);
                const int physical = (cycle - fetchCycle) / int(period);
                const bool level = cycle >= fetchCycle && physical >= int(delay) &&
                                   (!width || physical < int(delay + width));
                dut.irq = !nmi && level;
                dut.nmi = nmi && level;
                dut.eval();
                if (!dut.reset && dut.enable && dut.instruction && pc == 0x3000) {
                    actualPc = (ram[0x1ff] << 8) | ram[0x1fe];
                    actualCycles = elapsed;
                    actualFlags = ram[0x1fd];
                    break;
                }
                auto data = ram[dut.address];
                if (!dut.reset && dut.enable && dut.write_enable)
                    ram[dut.address] = dut.write_data;
                const bool enabled = dut.enable;
                dut.clk = 1;
                dut.eval();
                if (enabled)
                    dut.read_data = data;
                dut.eval();
            }
            count++;
            if (actualPc != expectedPc || actualCycles != expectedCycles ||
                actualFlags != expectedFlags) {
                failures++;
                std::cerr << signal << " fixture " << kind << " delay=" << delay
                          << " width=" << width << ": expected " << expectedPc << "/"
                          << expectedCycles << "/" << expectedFlags << ", actual " << actualPc
                          << "/" << actualCycles << "/" << actualFlags << "\n";
            }
        }
        if (!fixtures.eof() || count != 672)
            throw std::runtime_error("Truncated/malformed interrupt fixture suite");
        std::cout << count - failures << "/" << count << " NMOS " << signal
                  << " timing fixtures passed\n";
        return failures ? 1 : 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
