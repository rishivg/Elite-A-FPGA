#include "VAdc7002.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char **argv) {
    try {
        if (argc != 3)
            throw std::runtime_error("usage: ADC sampling FIXTURE CLOCK_MULTIPLIER");
        const unsigned multiplier = std::stoul(argv[2]);
        if (multiplier != 1 && multiplier != 3)
            throw std::runtime_error("Unsupported sampling master clock");
        std::ifstream fixture(argv[1]);
        if (!fixture)
            throw std::runtime_error("Missing sampling fixture");
        unsigned cases = 0, observations = 0, differences = 0;
        std::string kind;
        while (fixture >> kind && kind == "case") {
            unsigned id, count;
            if (!(fixture >> id >> count) || id != cases || count < 10 || count > 64)
                throw std::runtime_error("Invalid sampling case");
            VAdc7002 dut;
            dut.io_tick = dut.io_read = dut.io_write = dut.io_address = dut.io_data = 0;
            dut.io_channels = 0xfedc89ad456e123fULL;
            auto edge = [&]() {
                dut.clk = 0;
                dut.eval();
                dut.clk = 1;
                dut.eval();
            };
            dut.reset = 1;
            edge();
            edge();
            dut.reset = 0;
            unsigned current = 0;
            for (unsigned i = 0; i < count; ++i) {
                unsigned tick, write, data, status, hi, lo, eoc;
                uint64_t channels;
                if (!(fixture >> tick >> write >> data >> std::hex >> channels >> std::dec >>
                      status >> hi >> lo >> eoc) ||
                    tick > 60020 || write > 1 || data > 15 || status > 255 || hi > 255 ||
                    lo > 255 || eoc > 1 || (i && tick <= current) || (!i && tick != 0))
                    throw std::runtime_error("Invalid sampling observation");
                dut.io_address = 0;
                while (current < tick) {
                    const unsigned next = current + 1;
                    if (next == tick)
                        dut.io_channels = channels;
                    for (unsigned phase = 0; phase < 16 * multiplier; ++phase) {
                        const bool endpoint = phase == 16 * multiplier - 1;
                        dut.io_tick = (next & 1) && endpoint;
                        dut.io_write = next == tick && endpoint && write;
                        dut.io_data = data;
                        edge();
                    }
                    current = next;
                }
                dut.io_tick = dut.io_write = 0;
                dut.io_address = 0;
                dut.eval();
                const unsigned actualStatus = dut.io_result, actualEoc = dut.io_eocN;
                dut.io_address = 1;
                dut.eval();
                const unsigned actualHi = dut.io_result;
                dut.io_address = 2;
                dut.eval();
                const unsigned actualLo = dut.io_result;
                if (actualStatus != status || actualHi != hi || actualLo != lo ||
                    actualEoc != eoc) {
                    if (differences++ < 12)
                        std::cerr << "case " << id << " tick " << tick << ": status "
                                  << actualStatus << "/" << status << " data " << actualHi << ":"
                                  << actualLo << "/" << hi << ":" << lo << " eoc " << actualEoc
                                  << "/" << eoc << "\n";
                }
                observations++;
            }
            cases++;
        }
        unsigned expectedCases, expectedObservations;
        if (kind != "total" || !(fixture >> expectedCases >> expectedObservations) ||
            cases != 320 || expectedCases != cases || expectedObservations != observations ||
            fixture >> kind)
            throw std::runtime_error("Incomplete sampling fixture");
        std::cout << "ADC sampling: " << cases << " cases; " << observations << " observations; "
                  << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
