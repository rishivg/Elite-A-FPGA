// BRK padding, all architectural flags, stack wrap and vector boundaries.
#include "VBbcCpu.h"
#include "bbc_cpu_brk_stimulus.h"
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    const unsigned period = std::stoul(argv[2]);
    if (period != 1 && period != 16 && period != 48)
        return 2;
    std::ifstream fixture(argv[1]);
    if (!fixture)
        return 2;
    std::string line;
    unsigned count = 0, differences = 0;
    while (std::getline(fixture, line)) {
        std::istringstream row(line);
        if (line.rfind("total ", 0) == 0) {
            std::string total, extra;
            unsigned expected;
            if (!(row >> total >> expected) || row >> extra || expected != BrkCaseCount ||
                expected != count || std::getline(fixture, extra))
                return 2;
            std::cout << "BRK: " << count << " cases; " << differences << " differences\n";
            return differences ? 1 : 0;
        }
        unsigned id, pc, a, x, y, s, flags;
        if (!(row >> id >> pc >> a >> x >> y >> s >> flags) || id != count || id >= BrkCaseCount)
            return 2;
        std::vector<std::array<unsigned, 3>> expected;
        std::string entry;
        while (row >> entry) {
            std::istringstream item(entry);
            std::array<unsigned, 3> value;
            char separator1, separator2;
            std::string extra;
            if (!(item >> value[0] >> separator1 >> value[1] >> separator2 >> value[2]) ||
                separator1 != ':' || separator2 != ':' || item >> extra || value[0] > 65535 ||
                value[1] > 1 || value[2] > 255)
                return 2;
            expected.push_back(value);
        }
        if (expected.size() != 7)
            return 2;
        auto input = brk_stimulus(id);
        VBbcCpu dut;
        dut.irq = dut.nmi = dut.read_data = 0;
        dut.enable = 1;
        std::vector<std::array<unsigned, 3>> bus;
        bool started = false, done = false;
        unsigned first = 0, end = 0;
        for (unsigned cycle = 0; cycle < 100 * period; cycle++) {
            dut.reset = cycle < 8 * period;
            dut.enable = cycle % period == period - 1;
            dut.clk = 0;
            dut.eval();
            bool finish = false;
            if (!dut.reset && dut.enable && dut.instruction) {
                if (!started && uint16_t(dut.pc - 1) == input.target) {
                    started = true;
                    first = bus.size() - 1;
                } else if (started) {
                    finish = true;
                    end = uint16_t(dut.pc - 1);
                }
            }
            const uint8_t data = input.ram[dut.address];
            if (dut.enable)
                bus.push_back({dut.address, dut.write_enable,
                               unsigned(dut.write_enable ? dut.write_data : data)});
            if (!dut.reset && dut.enable && dut.write_enable)
                input.ram[dut.address] = dut.write_data;
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
        const bool valid = done && bus.size() == first + expected.size() + 2;
        const auto actual = valid ? std::vector<std::array<unsigned, 3>>(
                                        bus.begin() + first, bus.begin() + first + expected.size())
                                  : std::vector<std::array<unsigned, 3>>{};
        if (!valid || actual != expected || end != pc || dut.accumulator != a || dut.index_x != x ||
            dut.index_y != y || dut.stack_pointer != s || (dut.flags & 0xcf) != flags) {
            differences++;
            if (differences < 9)
                std::cerr << "BRK case " << id << " flags " << unsigned(dut.flags & 0xcf) << "/"
                          << flags << " PC " << end << "/" << pc << "\n";
        }
        count++;
    }
    return 2;
}
