#include "VVia6522.h"
#include "bbc_via_edges.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char **argv) {
    try {
        if (argc == 3 && std::string(argv[2]) == "edges")
            return via_edges(argv[1]);
        if (argc < 2 || argc > 3 || (argc == 3 && std::string(argv[2]) != "bus"))
            throw std::runtime_error("usage: VIA probe FIXTURE [bus]");
        const bool bus = argc == 3;
        std::ifstream fixture(argv[1]);
        if (!fixture)
            throw std::runtime_error("missing VIA fixture");
        std::string kind;
        unsigned cases = 0, samples = 0, differences = 0;
        while (fixture >> kind) {
            unsigned id, timer, continuous, value, ticks;
            if (kind != "case" || !(fixture >> id >> timer >> continuous >> value >> ticks) ||
                id != cases || timer < 1 || timer > 2 || continuous > 1 || value > 65535 ||
                ticks > 65541)
                throw std::runtime_error("invalid VIA case");
            VVia6522 via;
            via.io_tick = 0;
            via.io_phi2 = 0;
            via.io_read = 0;
            via.io_write = 0;
            via.io_address = 0;
            via.io_data = 0;
            via.io_portAIn = 255;
            via.io_portBIn = 255;
            via.io_ca1 = 0;
            via.io_ca2 = 0;
            via.io_ca2AfterWrite = 0;
            via.io_cb1 = 0;
            via.io_cb1Pulse = 0;
            via.io_cb2 = 0;
            auto edge = [&]() {
                via.clk = 0;
                via.eval();
                via.clk = 1;
                via.eval();
            };
            via.reset = 1;
            edge();
            edge();
            via.reset = 0;
            auto clock = [&]() {
                for (unsigned phase = 0; phase < 32; phase++) {
                    via.io_tick = phase == 31;
                    via.io_phi2 = phase >= 16;
                    edge();
                }
            };
            auto write = [&](unsigned address, unsigned data) {
                via.io_address = address;
                via.io_data = data;
                for (unsigned phase = 0; phase < 32; phase++) {
                    via.io_tick = phase == 31;
                    via.io_phi2 = phase >= 16;
                    via.io_write = phase == 31;
                    edge();
                }
                via.io_write = 0;
                via.io_tick = 0;
            };
            auto peek = [&](unsigned address) {
                via.io_address = address;
                via.eval();
                return unsigned(via.io_result);
            };
            write(14, 0xe0);
            write(11, 0x80 | (continuous ? 0x40 : 0));
            write(timer == 1 ? 4 : 8, value & 255);
            write(timer == 1 ? 5 : 9, value >> 8);
            unsigned previous = 0, observations = 0;
            while (fixture >> kind) {
                if (kind == "case") {
                    fixture.seekg(-std::streamoff(kind.size()), std::ios::cur);
                    break;
                }
                unsigned sampleId, tick, count, flags, pb7, after = 0;
                if (kind != "sample" || !(fixture >> sampleId >> tick >> count >> flags >> pb7) ||
                    (bus && !(fixture >> after)) || sampleId != id || tick > ticks ||
                    (observations && tick <= previous) || count > 65535 || flags > 255 || pb7 > 1 ||
                    after > 255)
                    throw std::runtime_error("invalid VIA sample");
                // The BBC samples peripheral data immediately before the enabled edge.
                // In bus mode also sample IRQ after selecting the acknowledging read.
                while (previous + (bus && tick ? 1 : 0) < tick) {
                    clock();
                    previous++;
                }
                via.io_tick = 0;
                if (bus && tick) {
                    for (unsigned phase = 0; phase < 31; phase++) {
                        via.io_phi2 = phase >= 16;
                        edge();
                    }
                    via.io_tick = 1;
                    via.io_phi2 = 1;
                }
                const unsigned actualFlags = peek(13), actualPb7 = peek(0) >> 7;
                const unsigned actual = peek(timer == 1 ? 4 : 8) | (peek(timer == 1 ? 5 : 9) << 8);
                unsigned actualAfter = actualFlags, actualIrq = via.io_irq;
                if (bus && tick) {
                    via.io_address = timer == 1 ? 4 : 8;
                    via.io_read = 1;
                    via.eval();
                    actualIrq = via.io_irq;
                    edge();
                    via.io_read = 0;
                    via.io_tick = 0;
                    actualAfter = peek(13);
                    previous = tick;
                }
                if (actual != count || actualFlags != flags || actualPb7 != pb7 ||
                    (bus && (actualAfter != after || actualIrq != ((after >> 7) & 1)))) {
                    if (differences++ < 12)
                        std::cerr << "case " << id << " tick " << tick << ": counter " << actual
                                  << "/" << count << ", flags " << actualFlags << "/" << flags
                                  << ", PB7 " << actualPb7 << "/" << pb7 << ", acknowledged flags "
                                  << actualAfter << "/" << after << ", IRQ " << actualIrq << "/"
                                  << ((after >> 7) & 1) << "\n";
                }
                samples++;
                observations++;
            }
            if (!observations || previous != ticks)
                throw std::runtime_error("truncated VIA observations");
            cases++;
        }
        if (cases != 32 || !fixture.eof())
            throw std::runtime_error("incomplete VIA fixture");
        std::cout << "VIA " << (bus ? "bus" : "passive") << ": " << samples << " observations in "
                  << cases << " cases; " << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
