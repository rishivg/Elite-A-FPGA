// Verify every interrupt bus slot, including discarded opcode and stack reads.
#include "VBbcCpu.h"
#include "interrupt_stimulus.h"
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
using Bus = std::array<unsigned, 3>;
int cpu_interrupt_bus_test(int argc, char **argv) {
    try {
        if (argc < 3 || argc > 4)
            throw std::runtime_error("Expected bus fixture, irq/nmi and optional enable period");
        const std::string signal = argv[2];
        if (signal != "irq" && signal != "nmi")
            throw std::runtime_error("Invalid signal");
        const bool nmi = signal == "nmi";
        const unsigned period = argc == 4 ? std::stoul(argv[3]) : 1;
        if (!period || period > 48)
            throw std::runtime_error("Invalid enable period");
        std::ifstream fixtures(argv[1]);
        if (!fixtures)
            throw std::runtime_error("Missing interrupt bus fixtures");
        unsigned count = 0, diffs = 0;
        std::string line;
        std::set<std::array<unsigned, 3>> seen;
        while (std::getline(fixtures, line)) {
            std::istringstream input(line);
            unsigned kind, delay, width, duration, base;
            if (!(input >> kind >> delay >> width >> duration) || kind >= 14 || delay >= 16 ||
                width > 2 || duration != 40 || !seen.insert({kind, delay, width}).second)
                throw std::runtime_error("Malformed or duplicate interrupt bus fixture");
            std::vector<Bus> gold;
            std::string entry;
            while (input >> entry) {
                std::istringstream request(entry);
                Bus row;
                char a, b;
                std::string extra;
                if (!(request >> row[0] >> a >> row[1] >> b >> row[2]) || a != ':' || b != ':' ||
                    row[0] > 65535 || row[1] > 1 || row[2] > 255 || request >> extra)
                    throw std::runtime_error("Malformed interrupt bus request");
                gold.push_back(row);
            }
            if (gold.size() != duration)
                throw std::runtime_error("Truncated interrupt bus sequence");
            auto initial = interrupt_ram(kind, nmi ? 0xfffa : 0xfffe, base);
            auto ram = initial;
            int fetch = -1;
            {
                VBbcCpu v;
                v.irq = 0;
                v.nmi = 0;
                v.enable = 1;
                v.read_data = 0;
                for (int cycle = 0; cycle < 250 * int(period); cycle++) {
                    v.reset = cycle < 8 * int(period);
                    v.enable = cycle % period == period - 1;
                    v.clk = 0;
                    v.eval();
                    if (!v.reset && v.enable && v.instruction && uint16_t(v.pc - 1) == base) {
                        fetch = cycle - int(period);
                        break;
                    }
                    auto d = ram[v.address];
                    if (!v.reset && v.enable && v.write_enable)
                        ram[v.address] = v.write_data;
                    v.clk = 1;
                    v.eval();
                    if (v.enable)
                        v.read_data = d;
                    v.eval();
                }
            }
            if (fetch < 0)
                return 2;
            ram = initial;
            VBbcCpu v;
            v.irq = 0;
            v.nmi = 0;
            v.enable = 1;
            v.read_data = 0;
            std::vector<Bus> actual;
            for (int cycle = 0; cycle < 250 * int(period); cycle++) {
                v.reset = cycle < 8 * int(period);
                v.enable = cycle % period == period - 1;
                v.clk = 0;
                v.eval();
                if (kind == 9 && cycle == fetch) {
                    ram[0x1fd] = 0x20;
                    ram[0x1fe] = 0x20;
                    ram[0x1ff] = 4;
                }
                const int elapsed = (cycle - fetch) / int(period);
                const bool level = cycle >= fetch && elapsed >= int(delay) &&
                                   (!width || elapsed < int(delay + width));
                v.nmi = nmi && level;
                v.irq = !nmi && level;
                v.eval();
                const uint8_t d = ram[v.address];
                if (v.enable && cycle >= fetch) {
                    actual.push_back(
                        {v.address, v.write_enable, unsigned(v.write_enable ? v.write_data : d)});
                    if (actual.size() == 40)
                        break;
                }
                if (!v.reset && v.enable && v.write_enable)
                    ram[v.address] = v.write_data;
                v.clk = 1;
                v.eval();
                if (v.enable)
                    v.read_data = d;
                v.eval();
            }
            count++;
            if (actual != gold && diffs++ < 12) {
                std::cerr << kind << "/" << delay << "/" << width << "\n";
                for (unsigned i = 0; i < gold.size(); i++)
                    if (i >= actual.size() || actual[i] != gold[i]) {
                        auto row = gold[i];
                        std::cerr << "slot " << i << " expected " << row[0] << ":" << row[1] << ":"
                                  << row[2];
                        if (i < actual.size()) {
                            row = actual[i];
                            std::cerr << " actual " << row[0] << ":" << row[1] << ":" << row[2];
                        }
                        std::cerr << "\n";
                    }
            }
        }
        if (count != 672)
            throw std::runtime_error("Incomplete interrupt bus fixture suite");
        std::cout << count << " complete " << signal << " bus traces; " << diffs
                  << " differences; enable period " << period << "\n";
        return diffs ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
