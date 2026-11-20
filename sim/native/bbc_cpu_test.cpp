// Independent published NMOS functional program running on synthesizable RTL.
#include "VBbcCpu.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>

int cpu_functional_test(int argc, char **argv) {
    try {
        if (argc < 2 || argc > 3)
            throw std::runtime_error(
                "Expected 64 KiB functional binary and optional clock-enable period");
        const unsigned period = argc == 3 ? std::stoul(argv[2]) : 1;
        if (period == 0 || period > 16)
            throw std::runtime_error("Invalid enable period");
        std::array<uint8_t, 65536> ram{};
        std::ifstream file(argv[1], std::ios::binary);
        file.read(reinterpret_cast<char *>(ram.data()), ram.size());
        if (file.gcount() != 65536)
            throw std::runtime_error("Functional binary is truncated");
        // Published test starts at $0400 and traps success at $3469.
        ram[0xfffc] = 0;
        ram[0xfffd] = 4;
        VBbcCpu dut;
        dut.enable = 1;
        dut.irq = 0;
        dut.nmi = 0;
        dut.read_data = 0;
        uint16_t last_pc = 0;
        uint64_t repeats = 0;
        for (uint64_t cycle = 0; cycle < uint64_t(150000000) * period; cycle++) {
            dut.enable = cycle % period == 0;
            dut.reset = cycle < 8;
            dut.clk = 0;
            dut.eval();
            uint8_t data = ram[dut.address];
            if (!dut.reset && dut.enable && dut.write_enable)
                ram[dut.address] = dut.write_data;
            dut.clk = 1;
            dut.eval();
            // Keep every paused clock edge in the test. Only avoid the extra
            // combinational evaluation when synchronous RAM returns the same
            // byte (including all clock-enable pauses).
            if (dut.enable && dut.read_data != data) {
                dut.read_data = data;
                dut.eval();
            }
            if (dut.instruction && !dut.reset && dut.enable) {
                uint16_t pc = static_cast<uint16_t>(dut.pc - 1);
                if (pc == last_pc)
                    repeats++;
                else {
                    last_pc = pc;
                    repeats = 0;
                }
                if (repeats >= 10) {
                    if (pc != 0x3469) {
                        std::cerr << "NMOS test trapped at $" << std::hex << pc
                                  << " A=" << unsigned(dut.accumulator)
                                  << " X=" << unsigned(dut.index_x)
                                  << " Y=" << unsigned(dut.index_y) << " P=" << unsigned(dut.flags)
                                  << "\n";
                        return 1;
                    }
                    std::cout << "NMOS functional test passed at $3469 after " << std::dec << cycle
                              << " master cycles, enable period " << period << "\n";
                    return 0;
                }
            }
        }
        throw std::runtime_error("Functional program did not complete");
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
