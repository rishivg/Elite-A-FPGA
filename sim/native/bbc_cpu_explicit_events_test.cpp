// Declared pin/event stimuli and independent native CPU firmware histories.
#include "VBbcCpuEvents.h"
#define CHIPS_IMPL
#include "../../build/bbc/reference-cycle/m6502.h"
#include "../../tools/bbc/reference/nmi.h"
#include <array>
#include <iostream>
#include <stdexcept>

int main() {
    try {
        unsigned cases = 0;
        for (unsigned period : {1, 16, 48}) {
            for (unsigned phase = 0; phase < period; ++phase) {
                for (bool withdraw : {false, true}) {
                    std::array<uint8_t, 65536> actual{}, reference{};
                    actual.fill(0xea);
                    actual[0x2000] = 0;
                    actual[0x400] = 0x4c;
                    actual[0x401] = 0;
                    actual[0x402] = 4;
                    actual[0x500] = 0xee;
                    actual[0x501] = 0;
                    actual[0x502] = 0x20;
                    actual[0x503] = 0x40;
                    actual[0xfffa] = 0;
                    actual[0xfffb] = 5;
                    actual[0xfffc] = 0;
                    actual[0xfffd] = 4;
                    reference = actual;
                    m6502_t source;
                    const m6502_desc_t description{};
                    uint64_t pins = m6502_init(&source, &description);
                    VBbcCpuEvents cpu;
                    cpu.reset = 1;
                    cpu.enable = 0;
                    cpu.nmi = 0;
                    cpu.nmi_event = 0;
                    cpu.irq = 0;
                    cpu.warm_reset = 0;
                    cpu.read_data = 0xea;
                    for (unsigned i = 0; i < 4; ++i) {
                        cpu.clk = 0;
                        cpu.eval();
                        cpu.clk = 1;
                        cpu.eval();
                    }
                    cpu.reset = 0;
                    bool level = false;
                    for (unsigned cycle = 1; cycle <= 256 * period; ++cycle) {
                        const bool accepted = cycle % period == 0;
                        const bool first = cycle == 64 * period;
                        const bool retrigger = cycle == 160 * period + phase;
                        if (first) {
                            level = true;
                            bbc_sample_nmi(&source, true);
                        }
                        if (retrigger) {
                            bbc_sample_nmi(&source, false);
                            bbc_sample_nmi(&source, true);
                            if (withdraw) {
                                level = false;
                                bbc_sample_nmi(&source, false);
                            }
                        }
                        cpu.clk = 0;
                        cpu.enable = accepted;
                        cpu.nmi = level;
                        cpu.nmi_event = retrigger;
                        cpu.eval();
                        const uint8_t response = actual[cpu.address];
                        if (accepted) {
                            if (cpu.write_enable)
                                actual[cpu.address] = cpu.write_data;
                            const uint16_t address = M6502_GET_ADDR(pins);
                            if (pins & M6502_RW) {
                                M6502_SET_DATA(pins, reference[address]);
                            } else {
                                reference[address] = M6502_GET_DATA(pins);
                            }
                            pins = (pins & ~M6502_NMI) | (level ? M6502_NMI : 0);
                            pins = m6502_tick(&source, pins);
                        }
                        cpu.clk = 1;
                        cpu.eval();
                        if (accepted) {
                            cpu.read_data = response;
                            cpu.eval();
                        }
                    }
                    if (actual[0x2000] != 2 || reference[0x2000] != 2) {
                        std::cout << "CPU_EVENT_MISMATCH period " << period << " phase " << phase
                                  << " withdraw " << withdraw << " handlers " << +actual[0x2000]
                                  << '/' << +reference[0x2000] << '\n';
                        return 1;
                    }
                    ++cases;
                }
            }
        }
        std::cout << "CPU explicit events: " << cases << " cold histories; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
