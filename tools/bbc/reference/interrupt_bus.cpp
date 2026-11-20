// Complete interrupt bus goldens from the pinned independent NMOS core.
#define CHIPS_IMPL
#include "interrupt_stimulus.h"
#include "m6502.h"
#include <fstream>
#include <iostream>
using Bus = std::array<unsigned, 3>;
int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    const std::string signal = argv[2];
    if (signal != "irq" && signal != "nmi")
        return 2;
    const bool nmi = signal == "nmi";
    std::ifstream fixtures(argv[1]);
    if (!fixtures)
        return 2;
    unsigned kind, delay, width, expectedPc, expectedFlags, count = 0;
    int expectedCycles;
    while (fixtures >> kind >> delay >> width >> expectedPc >> expectedCycles >> expectedFlags) {
        if (kind >= 14 || delay >= 16 || width > 2)
            return 2;
        unsigned base;
        auto ram = interrupt_ram(kind, nmi ? 0xfffa : 0xfffe, base);
        m6502_t source;
        m6502_desc_t desc{};
        uint64_t pins = m6502_init(&source, &desc);
        int baseline = -1;
        std::vector<Bus> gold;
        for (int cycle = 0; cycle < 250; cycle++) {
            if (cycle < 8)
                pins |= M6502_RES;
            else
                pins &= ~M6502_RES;
            const unsigned address = M6502_GET_ADDR(pins);
            if (cycle >= 8 && (pins & M6502_SYNC) && address == base && baseline < 0) {
                baseline = cycle;
                if (kind == 9) {
                    ram[0x1fd] = 0x20;
                    ram[0x1fe] = 0x20;
                    ram[0x1ff] = 4;
                }
            }
            const int elapsed = cycle - baseline;
            if (baseline >= 0 && elapsed >= int(delay) && (!width || elapsed < int(delay + width)))
                pins |= nmi ? M6502_NMI : M6502_IRQ;
            else
                pins &= ~(nmi ? M6502_NMI : M6502_IRQ);
            if (baseline >= 0) {
                gold.push_back({address, unsigned(!(pins & M6502_RW)),
                                unsigned(pins & M6502_RW ? ram[address] : M6502_GET_DATA(pins))});
                if (gold.size() == 40)
                    break;
            }
            pins = m6502_tick(&source, pins);
            const unsigned next = M6502_GET_ADDR(pins);
            if (pins & M6502_RW) {
                M6502_SET_DATA(pins, ram[next]);
            } else if (cycle >= 8)
                ram[next] = M6502_GET_DATA(pins);
        }
        if (gold.size() != 40)
            return 2;
        std::cout << kind << " " << delay << " " << width << " " << gold.size();
        for (auto row : gold)
            std::cout << " " << row[0] << ":" << row[1] << ":" << row[2];
        std::cout << "\n";
        count++;
    }
    return !fixtures.eof() || count != 672 ? 1 : 0;
}
