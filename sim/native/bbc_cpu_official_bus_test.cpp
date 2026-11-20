// All 151 documented opcodes: real bus requests and architectural results.
#include "../../tools/bbc/reference/official_stimulus.h"
#include "VBbcCpu.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
int cpu_official_bus_test(int argc, char **argv) {
    try {
        if (argc < 2 || argc > 4 || (argc == 4 && std::string(argv[3]) != "all-values"))
            throw std::runtime_error("Expected official bus fixtures and optional enable period");
        const bool allValues = argc == 4;
        unsigned period = argc > 2 ? std::stoul(argv[2]) : 1, count = 0, failures = 0;
        std::string line;
        std::ifstream input(argv[1]);
        if (!input || period < 1 || period > 48)
            throw std::runtime_error("Invalid official fixture or enable period");
        std::array<unsigned, 256> failed{};
        std::set<std::array<unsigned, 5>> seen;
        while (std::getline(input, line)) {
            std::istringstream row(line);
            unsigned op, mode, value, flags, boundary, duration, pc, a, x, y, s, p;
            if (!(row >> op >> mode >> value >> flags >> boundary >> duration >> pc >> a >> x >>
                  y >> s >> p) ||
                std::find(OfficialOpcodes.begin(), OfficialOpcodes.end(),
                          std::array<unsigned, 2>{op, mode}) == OfficialOpcodes.end() ||
                value > 255 ||
                (!allValues && value != 0 && value != 1 && value != 128 && value != 255) ||
                (flags & ~0x49u) || boundary >= (mode == Relative ? 3u : 2u) || duration < 2 ||
                duration > 7 || pc > 65535 || a > 255 || x > 255 || y > 255 || s > 255 ||
                (p & ~0xcfu) || !seen.insert({op, mode, value, flags, boundary}).second)
                throw std::runtime_error("Malformed or duplicate official bus fixture");
            std::vector<std::array<unsigned, 3>> expected;
            std::string entry;
            while (row >> entry) {
                std::istringstream e(entry);
                std::array<unsigned, 3> r;
                char c1, c2;
                std::string extra;
                if (!(e >> r[0] >> c1 >> r[1] >> c2 >> r[2]) || c1 != ':' || c2 != ':' ||
                    e >> extra || r[0] > 65535 || r[1] > 1 || r[2] > 255)
                    throw std::runtime_error("Malformed official bus request");
                expected.push_back(r);
            }
            if (expected.size() != duration)
                throw std::runtime_error("Truncated official bus sequence");
            auto f = official_stimulus(op, mode, value, flags, boundary);
            VBbcCpu dut;
            dut.irq = 0;
            dut.nmi = 0;
            dut.enable = 1;
            dut.read_data = 0;
            std::vector<std::array<unsigned, 3>> bus;
            unsigned first = 0, end = 0;
            bool started = false, done = false;
            for (unsigned cycle = 0; cycle < 300 * period; cycle++) {
                dut.reset = cycle < 8 * period;
                dut.enable = cycle % period == period - 1;
#ifdef BBC_CPU_COMPACT_IDLE
                // The bound all-register proof permits skipping disabled
                // edges with reset/NMI inactive and external inputs held.
                // Preserve all reset edges and every accepted bus endpoint.
                if (!dut.reset && !dut.enable) {
                    cycle += period - 2 - (cycle % period);
                    continue;
                }
#endif
                dut.clk = 0;
                dut.eval();
                bool finish = false;
                if (!dut.reset && dut.enable && dut.instruction) {
                    if (!started && uint16_t(dut.pc - 1) == f.pc) {
                        started = true;
                        first = bus.size() - 1;
                    } else if (started) {
                        finish = true;
                        end = uint16_t(dut.pc - 1);
                    }
                }
                unsigned char data = f.ram[dut.address];
                if (dut.enable)
                    bus.push_back({dut.address, dut.write_enable,
                                   unsigned(dut.write_enable ? dut.write_data : data)});
                if (!dut.reset && dut.enable && dut.write_enable)
                    f.ram[dut.address] = dut.write_data;
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
            bool valid = done && bus.size() == first + duration + 2;
            std::vector<std::array<unsigned, 3>> actual =
                valid ? std::vector<std::array<unsigned, 3>>(bus.begin() + first,
                                                             bus.begin() + first + duration)
                      : std::vector<std::array<unsigned, 3>>{};
            if (!valid || actual != expected || end != pc || dut.accumulator != a ||
                dut.index_x != x || dut.index_y != y || dut.stack_pointer != s ||
                (dut.flags & 0xcf) != p) {
                failures++;
                failed[op]++;
                if (failures < 13) {
                    std::cerr << "op=" << op << " mode=" << mode << " value=" << value
                              << " flags=" << flags << " boundary=" << boundary
                              << " valid=" << valid << " pc=" << end << "/" << pc
                              << " regs=" << unsigned(dut.accumulator) << ":" << a
                              << " flags=" << unsigned(dut.flags & 0xcf) << ":" << p
                              << " duration=" << bus.size() - first - 2 << ":" << duration << "\n";
                    for (unsigned i = 0; i < expected.size(); i++)
                        if (i >= actual.size() || actual[i] != expected[i]) {
                            auto r = expected[i];
                            std::cerr << " cycle " << i << " ref " << r[0] << ":" << r[1] << ":"
                                      << r[2];
                            if (i < actual.size()) {
                                r = actual[i];
                                std::cerr << " dut " << r[0] << ":" << r[1] << ":" << r[2];
                            }
                            std::cerr << "\n";
                        }
                }
            }
            count++;
        }
        for (unsigned i = 0; i < 256; i++)
            if (failed[i])
                std::cerr << "failed opcode " << i << " count " << failed[i] << "\n";
        std::cout << count - failures << "/" << count << " bus fixtures period " << period << "\n";
        if (count != OfficialCaseCount * (allValues ? 64 : 1) || !input.eof())
            throw std::runtime_error("Incomplete official opcode matrix");
        return failures ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
