// Compare full NMOS bus requests, including dummy reads and old-value writes.
#include "VBbcCpu.h"
#include <array>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>
using Request = std::array<unsigned, 3>;
int cpu_bus_test(int argc, char **argv) {
    try {
        if (argc < 2 || argc > 3)
            throw std::runtime_error("Expected bus fixtures and optional enable period");
        const unsigned period = argc == 3 ? std::stoul(argv[2]) : 1;
        if (period < 1 || period > 32)
            throw std::runtime_error("Invalid enable period");
        std::ifstream input(argv[1]);
        if (!input)
            throw std::runtime_error("Missing bus fixture");
        unsigned count = 0, failures = 0;
        std::string line;
        std::set<std::array<unsigned, 4>> seen;
        while (std::getline(input, line)) {
            std::istringstream row(line);
            unsigned opcode, mode, value, carry, duration, a, x, y, s, flags;
            if (!(row >> opcode >> mode >> value >> carry >> duration >> a >> x >> y >> s >>
                  flags) ||
                mode > 5 || opcode > 255 || value > 255 || carry > 1 || duration > 8 ||
                duration < 2 || !seen.insert({opcode, mode, value, carry}).second)
                throw std::runtime_error("Malformed or duplicate bus fixture");
            std::vector<Request> expected;
            std::string entry;
            while (row >> entry) {
                std::istringstream request(entry);
                Request r;
                char colon1, colon2;
                std::string extra;
                if (!(request >> r[0] >> colon1 >> r[1] >> colon2 >> r[2]) || colon1 != ':' ||
                    colon2 != ':' || request >> extra || r[0] > 65535 || r[1] > 1 || r[2] > 255)
                    throw std::runtime_error("Malformed bus request");
                expected.push_back(r);
            }
            if (expected.size() != duration)
                throw std::runtime_error("Truncated bus request sequence");
            unsigned operand = mode < 2 || mode == 5 ? 0x20 : mode == 4 ? 0x20ff : 0x2000;
            unsigned target =
                mode == 5 ? 0x2000 : operand + (mode == 1 || mode == 3 || mode == 4 ? 1 : 0);
            std::vector<unsigned> code{
                0xa2, 1, 0xea, 0xea, 0xea, carry ? 0x38u : 0x18u, opcode, operand & 255};
            if (mode >= 2 && mode != 5)
                code.push_back(operand >> 8);
            const unsigned end = 0x400 + code.size();
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
            VBbcCpu dut;
            dut.irq = 0;
            dut.nmi = 0;
            dut.enable = 1;
            dut.read_data = 0;
            std::vector<Request> bus;
            bool started = false, ended = false;
            unsigned first = 0;
            for (unsigned cycle = 0; cycle < 300 * period; cycle++) {
                dut.reset = cycle < 8 * period;
                dut.enable = cycle % period == period - 1;
                dut.clk = 0;
                dut.eval();
                // Synchronous-memory DECODE follows opcode fetch by one enable. Using
                // the first read of its address would include an implied dummy read.
                if (!dut.reset && dut.enable && dut.instruction && uint16_t(dut.pc - 1) == 0x406 &&
                    !started) {
                    started = true;
                    first = bus.size() - 1;
                }
                const bool finish =
                    started && dut.enable && dut.instruction && uint16_t(dut.pc - 1) == end;
                const uint8_t data = ram[dut.address];
                if (dut.enable)
                    bus.push_back({dut.address, dut.write_enable,
                                   unsigned(dut.write_enable ? dut.write_data : data)});
                if (!dut.reset && dut.enable && dut.write_enable)
                    ram[dut.address] = dut.write_data;
                dut.clk = 1;
                dut.eval();
                if (dut.enable)
                    dut.read_data = data;
                dut.eval();
                // The prior instruction's general register commits on DECODE's edge.
                if (finish) {
                    ended = true;
                    break;
                }
            }
            const bool valid = ended && bus.size() == first + duration + 2;
            const std::vector<Request> actual =
                valid ? std::vector<Request>(bus.begin() + first, bus.begin() + first + duration)
                      : std::vector<Request>{};
            const bool passed = valid && actual == expected && dut.accumulator == a &&
                                dut.index_x == x && dut.index_y == y && dut.stack_pointer == s &&
                                (dut.flags & 0xc3) == flags;
            if (!passed && failures++ < 12) {
                std::cerr << "Bus fixture opcode=" << opcode << " mode=" << mode
                          << " value=" << value << " carry=" << carry << "\n";
                for (unsigned i = 0; i < expected.size(); i++)
                    if (i >= actual.size() || actual[i] != expected[i]) {
                        auto e = expected[i];
                        std::cerr << " cycle " << i << " expected " << e[0] << ":" << e[1] << ":"
                                  << e[2];
                        if (i < actual.size()) {
                            auto d = actual[i];
                            std::cerr << " actual " << d[0] << ":" << d[1] << ":" << d[2];
                        }
                        std::cerr << "\n";
                    }
            }
            count++;
        }
        if (count != 348)
            throw std::runtime_error("Incomplete bus fixture matrix");
        std::cout << count - failures << "/" << count
                  << " independent NMOS bus fixtures passed at enable period " << period << "\n";
        return failures ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
