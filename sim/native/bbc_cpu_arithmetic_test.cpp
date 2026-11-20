// Complete immediate arithmetic or logic operand and initial-flag domains.
#include "VBbcCpu.h"
#include "bbc_cpu_arithmetic_stimulus.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char **argv) {
    try {
        if (argc != 3)
            throw std::runtime_error("usage: arithmetic probe FIXTURE ENABLE_PERIOD");
        const unsigned period = std::stoul(argv[2]);
        if (period != 1 && period != 16 && period != 48)
            throw std::runtime_error("Unsupported arithmetic enable period");
        std::ifstream input(argv[1], std::ios::binary);
        std::array<unsigned char, 12> header{};
        if (!input.read(reinterpret_cast<char *>(header.data()), header.size()) ||
            header != BbcArithmeticHeader)
            throw std::runtime_error("Invalid arithmetic fixture header");
        std::array<uint8_t, 65536> ram{};
        unsigned cases = 0, differences = 0;
        for (unsigned operation = 0; operation < BbcArithmeticOperations; ++operation)
            for (unsigned flags = 0; flags < BbcArithmeticFlags; ++flags)
                for (unsigned accumulator = 0; accumulator < 256; ++accumulator)
                    for (unsigned operand = 0; operand < 256; ++operand) {
                        const int a = input.get(), p = input.get();
                        if (a < 0 || p < 0)
                            throw std::runtime_error("Incomplete arithmetic fixture");
                        arithmetic_program(ram, operation, flags, accumulator, operand);
                        VBbcCpu dut;
                        dut.irq = dut.nmi = dut.read_data = 0;
                        bool done = false;
                        for (unsigned cycle = 0; cycle < 80 * period; ++cycle) {
                            dut.reset = cycle < 8 * period;
                            dut.enable = cycle % period == period - 1;
#ifdef BBC_CPU_COMPACT_IDLE
                            // The maintained SAT proof covers every CPU register
                            // with reset/NMI inactive. Inputs and read_data hold
                            // until the next enabled slot. Keep all reset edges.
                            if (!dut.reset && !dut.enable) {
                                cycle += period - 2 - (cycle % period);
                                continue;
                            }
#endif
                            dut.clk = 0;
                            dut.eval();
                            const bool finish = !dut.reset && dut.enable && dut.instruction &&
                                                uint16_t(dut.pc - 1) == BbcArithmeticEnd;
                            const uint8_t data = ram[dut.address];
                            if (!dut.reset && dut.enable && dut.write_enable)
                                ram[dut.address] = dut.write_data;
                            dut.clk = 1;
                            dut.eval();
                            if (dut.enable)
                                dut.read_data = data;
                            dut.eval();
                            if (finish) {
                                done = true;
                                break;
                            }
                        }
                        if (!done || dut.accumulator != a || (dut.flags & 0xcf) != unsigned(p)) {
                            if (differences++ < 16)
                                std::cerr << BbcArithmeticNames[operation] << " A=" << accumulator
                                          << " B=" << operand << " C=" << (flags & 1)
                                          << " D=" << ((flags >> 1) & 1) << " prefix=" << flags
                                          << ": " << unsigned(dut.accumulator) << "/" << a
                                          << " flags " << unsigned(dut.flags & 0xcf) << "/" << p
                                          << " done=" << done << "\n";
                        }
                        ++cases;
                    }
        if (cases != BbcArithmeticCases || input.peek() != std::char_traits<char>::eof())
            throw std::runtime_error("Trailing or incomplete arithmetic cases");
        std::cout << "Arithmetic: " << cases << " cases; period " << period << "; " << differences
                  << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
