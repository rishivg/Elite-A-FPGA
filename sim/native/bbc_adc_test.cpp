#include "VBbcAdcViaProbe.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
int main(int argc, char **argv) {
    try {
        if (argc < 2 || argc > 3)
            throw std::runtime_error("usage: ADC/VIA probe FIXTURE [clock multiplier]");
        const unsigned multiplier = argc == 3 ? std::stoul(argv[2]) : 1;
        if (multiplier != 1 && multiplier != 3)
            throw std::runtime_error("Unsupported ADC probe master clock");
        const unsigned clocksPerSlot = 16 * multiplier;
        std::ifstream fixture(argv[1]);
        if (!fixture)
            throw std::runtime_error("Missing ADC fixture");
        unsigned cases = 0, observations = 0, differences = 0;
        std::string kind;
        while (fixture >> kind) {
            unsigned id, count;
            if (kind != "case" || !(fixture >> id >> count) || id != cases || count < 10 ||
                count > 19)
                throw std::runtime_error("Invalid ADC case");
            VBbcAdcViaProbe v;
            v.tick = 0;
            v.phi2 = 0;
            v.adc_read = 0;
            v.adc_write = 0;
            v.via_write = 0;
            v.via_read = 0;
            v.adc_address = 0;
            v.via_address = 13;
            v.data = 0;
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
            unsigned current = 0;
            for (unsigned i = 0; i < count; i++) {
                unsigned tick, op, address, data, status, hi, lo, flags, irq, eoc;
                if (!(fixture >> tick >> op >> address >> data >> status >> hi >> lo >> flags >>
                      irq >> eoc) ||
                    tick > 40008 || op > 4 || address > 15 || data > 255 || status > 255 ||
                    hi > 255 || lo > 255 || flags > 255 || irq > 1 || eoc > 1 ||
                    (i && tick <= current))
                    throw std::runtime_error("Invalid ADC observation");
                while (current < tick) {
                    const unsigned next = current + 1;
                    for (unsigned phase = 0; phase < clocksPerSlot; phase++) {
                        const bool endpoint = phase == clocksPerSlot - 1;
                        // BbcMachine PHI2 is high for all of its first 2 MHz
                        // slot, with the VIA tick at that slot's final edge.
                        v.phi2 = next & 1;
                        v.tick = (next & 1) && endpoint;
                        v.adc_read = next == tick && op == 4 && endpoint;
                        v.adc_write = next == tick && op == 2 && endpoint;
                        v.via_write = next == tick && op == 1 && endpoint;
                        v.via_read = next == tick && op == 3 && endpoint;
                        v.via_address = next == tick && (op == 1 || op == 3) ? address : 13;
                        v.adc_address = next == tick && op == 4 ? address : 0;
                        v.data = data;
                        if (next == tick && endpoint) {
                            v.clk = 0;
                            v.eval();
                        }
                        edge();
                    }
                    current++;
                }
                v.tick = 0;
                v.phi2 = !(current & 1);
                v.adc_read = 0;
                v.adc_write = 0;
                v.via_write = 0;
                v.via_read = 0;
                v.via_address = 13;
                v.adc_address = 0;
                v.eval();
                const unsigned actualStatus = v.adc_result, actualFlags = v.via_result,
                               actualIrq = v.irq, actualEoc = v.eoc;
                v.adc_address = 1;
                v.eval();
                const unsigned actualHi = v.adc_result;
                v.adc_address = 2;
                v.eval();
                const unsigned actualLo = v.adc_result;
                if (actualStatus != status || actualHi != hi || actualLo != lo ||
                    actualFlags != flags || actualIrq != irq || actualEoc != eoc) {
                    if (differences++ < 12)
                        std::cerr << "case " << id << " tick " << tick << ": status "
                                  << actualStatus << "/" << status << ", data "
                                  << (actualHi * 256 + actualLo) << "/" << (hi * 256 + lo)
                                  << ", IFR " << actualFlags << "/" << flags << ", IRQ "
                                  << actualIrq << "/" << irq << ", EOC " << actualEoc << "/" << eoc
                                  << "\n";
                }
                observations++;
            }
            cases++;
        }
        if (cases != 112 || observations != 2000 || !fixture.eof())
            throw std::runtime_error("Incomplete ADC fixture");
        std::cout << "ADC/VIA: " << observations << " observations in " << cases << " cases; "
                  << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
