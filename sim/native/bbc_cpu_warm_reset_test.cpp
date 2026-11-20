// Continuous digital restart requests on independently executed CPUs.
// The original receives RES; the DUT receives its restart-request input.
// Every physical edge, bus request, retirement and final RAM byte is checked.
// This does not model the electrical behavior of a held physical RESET pin.
#define CHIPS_IMPL
#include "VBbcCpu.h"
#include "m6502.h"
#include "official_stimulus.h"
#include <array>
#include <iostream>

int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    const unsigned period = std::stoul(argv[1]);
    if (period != 1 && period != 16 && period != 48)
        return 2;
    const std::string mode = argv[2];
    if (mode != "normal" && mode != "interrupt" && mode != "branch")
        return 2;
    const uint64_t expected = mode == "normal" ? 231936 : mode == "interrupt" ? 212608 : 135168;
    uint64_t cases = 0, clocks = 0;
    for (auto op : OfficialOpcodes)
        for (unsigned flags = 0; flags < 8; flags++)
            for (unsigned mask = 0; mask < 2; mask++)
                for (unsigned boundary = 0; boundary < 3; boundary++)
                    for (unsigned value : {0u, 1u, 128u, 255u})
                        for (unsigned after = 0; after < 8; after++)
                            for (unsigned pattern = 0; pattern < 11; pattern++) {
                                if ((mode == "normal" && pattern != 0) ||
                                    (mode == "interrupt" && (boundary != 0 || value != 128)) ||
                                    (mode == "branch" && op[1] != Relative))
                                    continue;
                                const unsigned initialFlags =
                                    (flags & 1) | ((flags & 2) ? 8 : 0) | ((flags & 4) ? 64 : 0);
                                auto f =
                                    official_stimulus(op[0], op[1], value, initialFlags, boundary);
                                f.ram[f.pc - 9] = (f.ram[f.pc - 9] & ~4u) | (mask ? 4 : 0);
                                f.ram[0xfffa] = f.ram[0xfffe] = 0;
                                f.ram[0xfffb] = 0x80;
                                f.ram[0xffff] = 0x90;
                                for (unsigned base : {0x8000u, 0x9000u}) {
                                    f.ram[base] = 0xe6;
                                    f.ram[base + 1] = 0xf0;
                                    f.ram[base + 2] = 0x4c;
                                    f.ram[base + 3] = base & 255;
                                    f.ram[base + 4] = base >> 8;
                                }
                                // A separate ordinary execution locates the requested
                                // boundary without copying any state into either CPU.
                                auto pilotRam = f.ram;
                                m6502_t pilot;
                                m6502_desc_t description{};
                                uint64_t pilotPins = m6502_init(&pilot, &description);
                                int baseline = -1, warmAt = -1;
                                for (unsigned cycle = 0; cycle < 100; cycle++) {
                                    if ((pilotPins & M6502_SYNC) &&
                                        M6502_GET_ADDR(pilotPins) == f.pc)
                                        baseline = cycle;
                                    if (baseline >= 0) {
                                        warmAt = baseline + after;
                                        break;
                                    }
                                    const unsigned address = M6502_GET_ADDR(pilotPins);
                                    if (pilotPins & M6502_RW) {
                                        M6502_SET_DATA(pilotPins, pilotRam[address]);
                                    } else {
                                        pilotRam[address] = M6502_GET_DATA(pilotPins);
                                    }
                                    pilotPins = m6502_tick(&pilot, pilotPins);
                                }
                                if (baseline < 0 || warmAt < 0)
                                    return 2;
                                auto ram = f.ram, actualRam = f.ram;
                                m6502_t original;
                                uint64_t pins = m6502_init(&original, &description);
                                VBbcCpu actual;
                                actual.reset = actual.enable = 1;
                                actual.irq = actual.nmi = actual.warm_reset = actual.read_data = 0;
                                for (unsigned edge = 0; edge < 8 * period; edge++) {
                                    actual.clk = 0;
                                    actual.eval();
                                    actual.clk = 1;
                                    actual.eval();
                                }
                                actual.reset = 0;
                                struct Architecture {
                                    unsigned cycle, pc, a, x, y, s, p;
                                } latest{};
                                for (unsigned cycle = 0; cycle < unsigned(baseline) + 64; cycle++) {
                                    const int relative = int(cycle) - warmAt;
                                    actual.irq = (pattern == 1 || pattern == 10) && relative >= -2;
                                    actual.nmi =
                                        (pattern >= 2 && pattern <= 4 &&
                                         relative == int(pattern) - 2) ||
                                        (pattern == 5 && relative == -2) ||
                                        (pattern == 6 && relative == -1) ||
                                        (pattern == 7 && (relative == -2 || relative == 1)) ||
                                        (pattern == 8 && (relative == -3 || relative == 0)) ||
                                        (pattern == 9 && relative == 3) ||
                                        (pattern == 10 && relative == 0);
                                    pins = (pins & ~(M6502_IRQ | M6502_NMI)) |
                                           (actual.irq ? M6502_IRQ : 0) |
                                           (actual.nmi ? M6502_NMI : 0);
                                    actual.warm_reset = int(cycle) == warmAt;
                                    if (actual.warm_reset)
                                        pins |= M6502_RES;
                                    actual.enable = 1;
                                    actual.clk = 0;
                                    actual.eval();
                                    if (pins & M6502_SYNC)
                                        latest = {cycle,
                                                  M6502_GET_ADDR(pins),
                                                  original.A,
                                                  original.X,
                                                  original.Y,
                                                  original.S,
                                                  unsigned(original.P & 0xcf)};
                                    if (bool(actual.sync) != bool(pins & M6502_SYNC)) {
                                        std::cerr << "SYNC FAIL op " << op[0] << " onset " << after
                                                  << " cycle " << cycle << " actual "
                                                  << unsigned(actual.sync) << " ref "
                                                  << bool(pins & M6502_SYNC) << "\n";
                                        return 1;
                                    }
                                    const bool retire = actual.instruction;
                                    const unsigned pc = uint16_t(actual.pc - 1),
                                                   oldI = actual.flags & 4;
                                    const unsigned address = M6502_GET_ADDR(pins);
                                    const bool write = !(pins & M6502_RW);
                                    const unsigned data =
                                        write ? M6502_GET_DATA(pins) : ram[address];
                                    if (actual.address != address ||
                                        bool(actual.write_enable) != write ||
                                        (write && actual.write_data != data)) {
                                        std::cerr << "BUS FAIL pattern " << pattern << " opcode "
                                                  << op[0] << " flags " << flags << " mask " << mask
                                                  << " boundary " << boundary << " value " << value
                                                  << " after " << after << " period " << period
                                                  << " cycle " << cycle << " original " << address
                                                  << ":" << write << ":" << data << " actual "
                                                  << actual.address << ":"
                                                  << unsigned(actual.write_enable) << ":"
                                                  << unsigned(actual.write_data) << "\n";
                                        return 1;
                                    }
                                    if (write) {
                                        ram[address] = data;
                                        actualRam[address] = actual.write_data;
                                    } else {
                                        M6502_SET_DATA(pins, ram[address]);
                                    }
                                    pins = m6502_tick(&original, pins);
                                    const uint8_t read = actualRam[actual.address];
                                    for (unsigned phase = 0; phase < period; phase++) {
                                        actual.enable = phase == period - 1;
                                        actual.clk = 0;
                                        actual.eval();
                                        actual.clk = 1;
                                        actual.eval();
                                        if (actual.enable)
                                            actual.read_data = read;
                                        // Requests last one physical edge, even between enables.
                                        if (phase == 0)
                                            actual.warm_reset = 0;
                                        actual.eval();
                                    }
                                    clocks++;
                                    if (retire && cycle >= unsigned(baseline) &&
                                        (latest.cycle + 1 != cycle || latest.pc != pc ||
                                         actual.accumulator != latest.a ||
                                         actual.index_x != latest.x || actual.index_y != latest.y ||
                                         actual.stack_pointer != latest.s ||
                                         ((actual.flags & 0xcb) | oldI) != latest.p)) {
                                        std::cerr << "ARCH FAIL pattern " << pattern << " opcode "
                                                  << op[0] << " flags " << flags << " mask " << mask
                                                  << " boundary " << boundary << " value " << value
                                                  << " after " << after << " period " << period
                                                  << " cycle " << cycle << " original " << latest.a
                                                  << ":" << latest.x << ":" << latest.y << ":"
                                                  << latest.s << ":" << latest.p << " actual "
                                                  << unsigned(actual.accumulator) << ":"
                                                  << unsigned(actual.index_x) << ":"
                                                  << unsigned(actual.index_y) << ":"
                                                  << unsigned(actual.stack_pointer) << ":"
                                                  << unsigned((actual.flags & 0xcb) | oldI) << "\n";
                                        return 1;
                                    }
                                }
                                if (ram != actualRam)
                                    return 1;
                                cases++;
                            }
    std::cout << "Warm reset " << mode << ": " << cases << " cases; " << clocks
              << " enabled cycles; period " << period << "; PASS\n";
    return cases == expected && clocks == expected * 90 ? 0 : 2;
}
