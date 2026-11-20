// Standalone waveform/protocol probe, independent of firmware sound effects.
#include "VPsg.h"
#include "VPsg___024root.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
struct Event {
    unsigned long cycle;
    unsigned value;
};
int main(int argc, char **argv) {
    try {
        if (argc != 5)
            throw std::runtime_error(
                "usage: validate_psg PERIOD NOISE_CONTROL OUTPUT_DIR DURATION_CYCLES | "
                "validate_psg --script EVENTS OUTPUT_DIR DURATION_CYCLES");
        auto integer = [](const char *s) {
            std::string text(s);
            if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
                throw std::runtime_error("invalid integer");
            return std::stoul(text);
        };
        const bool scripted = std::string(argv[1]) == "--script";
        unsigned long period = scripted ? 0 : integer(argv[1]),
                      noise = scripted ? 0 : integer(argv[2]), duration = integer(argv[4]);
        if (period > 1023 || noise > 7 || duration < 10000 || duration > 4000000)
            throw std::runtime_error("probe argument outside range");
        std::vector<Event> events;
        if (scripted) {
            std::ifstream input(argv[2]);
            if (!input)
                throw std::runtime_error("cannot read events");
            std::string line;
            while (std::getline(input, line)) {
                std::istringstream row(line);
                std::string c, v, extra;
                if (!(row >> c >> v) || row >> extra)
                    throw std::runtime_error("malformed event");
                auto cycle = integer(c.c_str()), value = integer(v.c_str());
                if (value > 255 || cycle >= duration ||
                    (!events.empty() && cycle <= events.back().cycle))
                    throw std::runtime_error("invalid event range/order");
                events.push_back({cycle, unsigned(value)});
            }
            if (events.empty())
                throw std::runtime_error("empty events");
        } else {
            // Also program tone 2, so controls 3/7 exercise its actual period.
            std::vector<unsigned> writes = {unsigned(0x80 | (period & 15)),
                                            unsigned(period >> 4),
                                            0x90,
                                            unsigned(0xA0 | (period & 15)),
                                            unsigned(period >> 4),
                                            0xBF,
                                            unsigned(0xC0 | (period & 15)),
                                            unsigned(period >> 4),
                                            0xDF,
                                            unsigned(0xE0 | noise),
                                            0xFF};
            for (size_t i = 0; i < writes.size(); ++i)
                events.push_back({i * 16, writes[i]});
        }
        std::string dir(argv[3]);
        std::ofstream tones(dir + "/tones.tsv"), shifts(dir + "/noise.tsv"),
            samples(dir + "/samples.tsv"), registers(dir + "/registers.tsv"),
            pdm(dir + "/pdm.json");
        if (!tones || !shifts || !samples || !registers || !pdm)
            throw std::runtime_error("cannot create probe output");
        VPsg top;
        auto *r = top.rootp;
        auto tick = [&]() {
            top.clk = 0;
            top.eval();
            top.clk = 1;
            top.eval();
        };
        top.reset = 1;
        top.io_write = 0;
        top.io_data = 0;
        for (int i = 0; i < 16; ++i)
            tick();
        top.reset = 0;
        // Identical 250 ns write pulses and guest timing are recorded by the JS probe.
        size_t next = 0;
        unsigned lastTone = top.io_tone, lastLfsr = r->Psg__DOT__lfsr;
        uint64_t sampleSum = 0, pdmOnes = 0;
        int64_t minError = 0, maxError = 0;
        for (unsigned long cycle = 0; cycle < duration; ++cycle) {
            top.io_write = next < events.size() && cycle == events[next].cycle;
            if (top.io_write)
                top.io_data = events[next++].value;
            sampleSum += top.io_sample;
            tick();
            pdmOnes += top.io_pdm;
            int64_t error = int64_t(pdmOnes * 256) - int64_t(sampleSum);
            if (error < -255 || error > 0)
                throw std::runtime_error("PDM violates cumulative sample conservation");
            minError = std::min(minError, error);
            maxError = std::max(maxError, error);
            if (top.io_write) {
                registers << cycle << '\t' << r->Psg__DOT__tonePeriod_0 << '\t'
                          << r->Psg__DOT__tonePeriod_1 << '\t' << r->Psg__DOT__tonePeriod_2 << '\t'
                          << unsigned(r->Psg__DOT__noiseCtrl) << '\t'
                          << unsigned(r->Psg__DOT__toneVol_0) << '\t'
                          << unsigned(r->Psg__DOT__toneVol_1) << '\t'
                          << unsigned(r->Psg__DOT__toneVol_2) << '\t'
                          << unsigned(r->Psg__DOT__noiseVol) << '\t'
                          << unsigned(r->Psg__DOT__latchedReg) << '\t'
                          << unsigned(r->Psg__DOT__latchedVol) << '\n';
            }
            if (lastTone != top.io_tone) {
                tones << cycle << '\t' << unsigned(top.io_tone) << '\n';
                lastTone = top.io_tone;
            }
            if (lastLfsr != r->Psg__DOT__lfsr) {
                shifts << cycle << '\t' << unsigned(r->Psg__DOT__lfsr) << '\n';
                lastLfsr = r->Psg__DOT__lfsr;
            }
            if (cycle % 8 == 7)
                samples << cycle << '\t' << unsigned(top.io_sample) << '\t' << unsigned(top.io_tone)
                        << '\t' << unsigned(top.io_noise) << '\n';
        }
        if (next != events.size())
            throw std::runtime_error("events were not consumed");
        pdm << "{\"passed\":true,\"cycles\":" << duration << ",\"sample_sum\":" << sampleSum
            << ",\"pdm_ones\":" << pdmOnes << ",\"min_integral_error\":" << minError
            << ",\"max_integral_error\":" << maxError << "}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
