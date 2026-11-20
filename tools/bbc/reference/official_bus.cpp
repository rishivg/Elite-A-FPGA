// Independent NMOS pin engine records full bus requests and architectural results.
#define CHIPS_IMPL
#include "m6502.h"
#include "official_stimulus.h"
#include <fstream>
#include <iostream>
#include <string>
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3 || (argc == 3 && std::string(argv[2]) != "all-values"))
        return 1;
    const bool allValues = argc == 3;
    std::vector<unsigned> values{0, 1, 128, 255};
    if (allValues) {
        values.clear();
        for (unsigned value = 0; value < 256; value++)
            values.push_back(value);
    }
    std::ofstream input(argv[1]);
    if (!input)
        return 1;
    unsigned cases = 0;
    for (auto op : OfficialOpcodes)
        for (unsigned value : values)
            for (unsigned flagCase = 0; flagCase < 8; flagCase++)
                for (unsigned boundary = 0; boundary < (op[1] == 16 ? 3u : 2u); boundary++) {
                    unsigned flags =
                        (flagCase & 1) | ((flagCase & 2) ? 8 : 0) | ((flagCase & 4) ? 64 : 0);
                    auto f = official_stimulus(op[0], op[1], value, flags, boundary);
                    input << f.pc - 13 << " " << f.pc;
                    for (auto r : f.inputs)
                        input << " " << r[0] << ":" << r[1];
                    input << "\n";
                    m6502_t cpu;
                    m6502_desc_t desc{};
                    uint64_t pins = m6502_init(&cpu, &desc);
                    std::vector<std::array<unsigned, 3>> bus;
                    bool started = false, done = false;
                    for (unsigned cycle = 0; cycle < 200; cycle++) {
                        unsigned address = M6502_GET_ADDR(pins);
                        if ((pins & M6502_SYNC) && address == f.pc && !started)
                            started = true;
                        else if (started && (pins & M6502_SYNC)) {
                            std::cout << op[0] << " " << op[1] << " " << value << " " << flags
                                      << " " << boundary << " " << bus.size() << " " << address
                                      << " " << unsigned(cpu.A) << " " << unsigned(cpu.X) << " "
                                      << unsigned(cpu.Y) << " " << unsigned(cpu.S) << " "
                                      << (cpu.P & 0xcf);
                            for (auto r : bus)
                                std::cout << " " << r[0] << ":" << r[1] << ":" << r[2];
                            std::cout << "\n";
                            done = true;
                            cases++;
                            break;
                        }
                        if (started)
                            bus.push_back({address, unsigned(!(pins & M6502_RW)),
                                           unsigned(pins & M6502_RW ? f.ram[address]
                                                                    : M6502_GET_DATA(pins))});
                        if (pins & M6502_RW) {
                            M6502_SET_DATA(pins, f.ram[address]);
                        } else
                            f.ram[address] = M6502_GET_DATA(pins);
                        pins = m6502_tick(&cpu, pins);
                    }
                    if (!done)
                        return 1;
                }
    input.flush();
    std::cout.flush();
    if (cases != OfficialCaseCount * (allValues ? 64 : 1) || !input || !std::cout)
        return 1;
    std::cerr << cases << " documented-opcode bus fixtures\n";
    return 0;
}
