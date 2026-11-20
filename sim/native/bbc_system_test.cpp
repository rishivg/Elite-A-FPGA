#include "VBbcSystemProbe.h"
#include "bbc_keyboard_irq.h"
#include <array>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char **argv) {
    try {
        if (argc == 3 && std::string(argv[1]) == "irq")
            return keyboard_irq(argv[2]);
        if (argc == 3 && std::string(argv[1]) == "key-edges")
            return keyboard_irq_edges(argv[2]);
        if (argc != 2)
            throw std::runtime_error("usage: system pin probe FIXTURE");
        std::ifstream f(argv[1]);
        if (!f)
            throw std::runtime_error("Missing system pin fixture");
        unsigned cases = 0, observations = 0, events = 0, differences = 0;
        std::string kind;
        while (f >> kind) {
            unsigned id, keyCount, busCount, soundCount;
            if (kind != "case" || !(f >> id >> keyCount >> busCount >> soundCount) || id != cases ||
                keyCount > 2 || busCount != 37 || soundCount != 11)
                throw std::runtime_error("Invalid system pin case");
            VBbcSystemProbe v;
            v.tick = 0;
            v.write = 0;
            v.address = 0;
            v.data = 0;
            for (unsigned i = 0; i < 4; i++)
                v.keys[i] = 0;
            for (unsigned i = 0; i < keyCount; i++) {
                unsigned column, row;
                if (!(f >> kind >> column >> row) || kind != "key" || column >= 10 || row >= 8)
                    throw std::runtime_error("Invalid physical key");
                const unsigned bit = column * 8 + row;
                v.keys[bit / 32] |= uint32_t(1) << (bit % 32);
            }
            std::vector<std::array<unsigned, 7>> bus;
            for (unsigned i = 0; i < busCount; i++) {
                std::array<unsigned, 7> row{};
                if (!(f >> kind) || kind != "bus")
                    throw std::runtime_error("Missing bus observation");
                for (auto &value : row)
                    if (!(f >> value))
                        throw std::runtime_error("Truncated bus observation");
                if (!row[0] || row[0] >= 6400 || row[0] % 32 != 16 || row[1] > 15 || row[2] > 255 ||
                    row[3] > 255 || row[4] > 255 || row[5] > 255 || row[6] > 3 ||
                    (!bus.empty() && row[0] <= bus.back()[0]))
                    throw std::runtime_error("Invalid bus observation");
                bus.push_back(row);
            }
            std::vector<std::array<unsigned, 2>> expected, actual;
            for (unsigned i = 0; i < soundCount; i++) {
                unsigned cycle, data;
                if (!(f >> kind >> cycle >> data) || kind != "sound" || cycle >= 6400 ||
                    data > 255 || (!expected.empty() && cycle <= expected.back()[0]))
                    throw std::runtime_error("Invalid sound event");
                expected.push_back({cycle, data});
            }
            auto edge = [&]() {
                v.clk = 0;
                v.eval();
                v.clk = 1;
                v.eval();
            };
            v.reset = 1;
            edge();
            edge();
            v.reset = 0;
            // Reset release is clock zero. The Model B's already-active WE
            // window starts there; the first accepted clock follows at one.
            unsigned next = 0;
            for (unsigned clock = 1; clock <= 6400; clock++) {
                v.tick = clock % 32 == 16;
                v.write = next < bus.size() && clock == bus[next][0];
                if (v.write) {
                    v.address = bus[next][1];
                    v.data = bus[next][2];
                }
                v.clk = 0;
                v.eval();
                if (v.sound_write)
                    actual.push_back({clock, v.sound_data});
                v.clk = 1;
                v.eval();
                if (v.write) {
                    const auto &row = bus[next++];
                    v.write = 0;
                    v.eval();
                    if (v.port_a != row[3] || v.port_b != row[4] || v.latch != row[5] ||
                        v.scroll != row[6]) {
                        if (differences++ < 12)
                            std::cerr << "case " << id << " clock " << clock << ": PA "
                                      << unsigned(v.port_a) << "/" << row[3] << ", PB "
                                      << unsigned(v.port_b) << "/" << row[4] << ", IC32 "
                                      << unsigned(v.latch) << "/" << row[5] << "\n";
                    }
                    observations++;
                }
            }
            if (actual != expected) {
                differences++;
                std::cerr << "case " << id << " sound event mismatch\n";
                for (unsigned i = 0; i < std::max(actual.size(), expected.size()); i++) {
                    if (i < actual.size())
                        std::cerr << " actual " << actual[i][0] << " " << actual[i][1];
                    if (i < expected.size())
                        std::cerr << " expected " << expected[i][0] << " " << expected[i][1];
                    std::cerr << "\n";
                }
            }
            events += actual.size();
            cases++;
        }
        if (cases != 4 || observations != 148 || !f.eof())
            throw std::runtime_error("Incomplete system pin fixture");
        std::cout << "System pins: " << observations << " bus observations and " << events
                  << " sound events in " << cases << " cases; " << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
