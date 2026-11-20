// Interrupt collisions across every documented opcode, using ordinary resets.
// The independent pin reference and RTL own their RAM, stack and bus execution.
// A separate reference pilot determines the stimulus clock origin; no CPU state
// is copied into either measured execution.
#define CHIPS_IMPL
#include "VBbcCpu.h"
#include "m6502.h"
#include "official_stimulus.h"
#include <array>
#include <iostream>
int main(int argc, char **argv) {
    unsigned period = argc == 2 ? std::stoul(argv[1]) : 1;
    if (period != 1 && period != 16 && period != 48)
        return 2;
    unsigned cases = 0, enabledCycles = 0;
    for (auto op : OfficialOpcodes)
        for (unsigned mask = 0; mask < 2; mask++)
            for (unsigned boundary = 0; boundary < 2; boundary++)
                for (unsigned value : {0u, 128u})
                    for (unsigned delay = 0; delay < 16; delay++)
                        for (unsigned signals = 0; signals < 5; signals++) {
                            auto f = official_stimulus(op[0], op[1], value, 0x49, boundary);
                            f.ram[f.pc - 9] = (f.ram[f.pc - 9] & ~4u) | (mask ? 4 : 0);
                            f.ram[0xfffa] = 0;
                            f.ram[0xfffb] = 0x80;
                            f.ram[0xfffe] = 0;
                            f.ram[0xffff] = 0x90;
                            for (unsigned base : {0x8000u, 0x9000u}) {
                                f.ram[base] = 0xe6;
                                f.ram[base + 1] = 0xf0;
                                f.ram[base + 2] = 0x4c;
                                f.ram[base + 3] = base & 255;
                                f.ram[base + 4] = base >> 8;
                            }
                            auto pilotRam = f.ram;
                            m6502_t pilot;
                            m6502_desc_t desc{};
                            uint64_t pilotPins = m6502_init(&pilot, &desc);
                            int baseline = -1;
                            for (unsigned cycle = 0; cycle < 100; cycle++) {
                                if ((pilotPins & M6502_SYNC) && M6502_GET_ADDR(pilotPins) == f.pc) {
                                    baseline = cycle;
                                    break;
                                }
                                unsigned a = M6502_GET_ADDR(pilotPins);
                                if (pilotPins & M6502_RW) {
                                    M6502_SET_DATA(pilotPins, pilotRam[a]);
                                } else
                                    pilotRam[a] = M6502_GET_DATA(pilotPins);
                                pilotPins = m6502_tick(&pilot, pilotPins);
                            }
                            if (baseline < 0)
                                return 2;
                            auto ram = f.ram, dutRam = f.ram;
                            m6502_t original;
                            uint64_t pins = m6502_init(&original, &desc);
                            VBbcCpu dut;
                            dut.reset = 1;
                            dut.enable = 1;
                            dut.irq = dut.nmi = dut.read_data = 0;
                            for (unsigned i = 0; i < 8 * period; i++) {
                                dut.clk = 0;
                                dut.eval();
                                dut.clk = 1;
                                dut.eval();
                            }
                            dut.reset = 0;
                            struct Arch {
                                unsigned cycle, pc, a, x, y, s, p;
                            } latest{};
                            bool entered = false;
                            for (unsigned cycle = 0; cycle < unsigned(baseline) + 64; cycle++) {
                                int elapsed = int(cycle) - baseline - int(delay);
                                bool irq = (signals == 0 || signals >= 2) && elapsed >= 0;
                                bool nmi = signals == 1   ? elapsed >= 0 && elapsed < 2
                                           : signals == 2 ? elapsed >= 5 && elapsed < 7
                                           : signals == 3 ? (elapsed >= 5 && elapsed < 7) ||
                                                                (elapsed >= 13 && elapsed < 15)
                                           : signals == 4 ? elapsed >= 0 && elapsed < 2
                                                          : false;
                                dut.irq = irq;
                                dut.nmi = nmi;
                                dut.enable = 1;
                                dut.clk = 0;
                                dut.eval();
                                pins = (pins & ~(M6502_IRQ | M6502_NMI)) | (irq ? M6502_IRQ : 0) |
                                       (nmi ? M6502_NMI : 0);
                                if (pins & M6502_SYNC)
                                    latest = {cycle,
                                              M6502_GET_ADDR(pins),
                                              original.A,
                                              original.X,
                                              original.Y,
                                              original.S,
                                              unsigned(original.P & 0xcf)};
                                bool retire = dut.instruction;
                                unsigned retirePc = uint16_t(dut.pc - 1), oldI = dut.flags & 4;
                                if (retire && retirePc == f.pc)
                                    entered = true;
                                unsigned a = M6502_GET_ADDR(pins);
                                bool write = !(pins & M6502_RW);
                                unsigned data = write ? M6502_GET_DATA(pins) : ram[a];
                                if (dut.address != a || bool(dut.write_enable) != write ||
                                    (write && dut.write_data != data)) {
                                    std::cerr << "BUS op " << op[0] << " mask " << mask
                                              << " boundary " << boundary << " value " << value
                                              << " delay " << delay << " signals " << signals
                                              << " cycle " << cycle << " expected " << a << ":"
                                              << write << ":" << data << " actual " << dut.address
                                              << ":" << unsigned(dut.write_enable) << ":"
                                              << unsigned(dut.write_data) << "\n";
                                    return 1;
                                }
                                if (write) {
                                    ram[a] = data;
                                    dutRam[a] = dut.write_data;
                                } else {
                                    M6502_SET_DATA(pins, ram[a]);
                                }
                                pins = m6502_tick(&original, pins);
                                uint8_t read = dutRam[dut.address];
                                for (unsigned phase = 0; phase < period; phase++) {
                                    dut.enable = phase == period - 1;
                                    dut.clk = 0;
                                    dut.eval();
                                    dut.clk = 1;
                                    dut.eval();
                                    if (dut.enable)
                                        dut.read_data = read;
                                    dut.eval();
                                }
                                if (retire && entered &&
                                    (latest.cycle + 1 != cycle || latest.pc != retirePc ||
                                     dut.accumulator != latest.a || dut.index_x != latest.x ||
                                     dut.index_y != latest.y || dut.stack_pointer != latest.s ||
                                     ((dut.flags & 0xcb) | oldI) != latest.p)) {
                                    std::cerr << "ARCH op " << op[0] << " mask " << mask
                                              << " boundary " << boundary << " value " << value
                                              << " delay " << delay << " signals " << signals
                                              << " cycle " << cycle << "\n";
                                    return 1;
                                }
                            }
                            if (!entered || ram != dutRam)
                                return 2;
                            cases++;
                            enabledCycles += unsigned(baseline) + 64;
                        }
    std::cout << "Interrupt matrix: " << cases << " cases; " << enabledCycles
              << " enabled cycles; period " << period << "; PASS\n";
    return 0;
}
