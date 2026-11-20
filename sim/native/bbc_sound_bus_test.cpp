#ifndef BBC_MASTER_MULTIPLIER
#define BBC_MASTER_MULTIPLIER 1
#endif
#include "VBbcSoundBusTest.h"
#include "bbc_audio.h"
#include <array>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
int main(int argc, char **argv) {
    try {
        if (argc != 4)
            throw std::runtime_error("usage: probe EVENTS OUTPUT CYCLES");
        unsigned cycles = std::stoul(argv[3]);
        std::ifstream input(argv[1]);
        std::vector<std::array<unsigned, 4>> events;
        unsigned cycle, enabled, touch, data;
        if (!input || !cycles || cycles % 64)
            throw std::runtime_error("invalid fixture or capture duration");
        while (input >> cycle >> enabled >> touch >> data) {
            if (cycle >= cycles || enabled > 1 || touch > 1 || data > 255 ||
                (!events.empty() && cycle <= events.back()[0]))
                throw std::runtime_error("invalid bus event");
            events.push_back({cycle, enabled, touch, data});
        }
        if (!input.eof())
            throw std::runtime_error("invalid fixture");
        std::ofstream out(argv[2], std::ios::binary),
            pins(std::string(argv[2]) + ".pins", std::ios::binary),
            writes(std::string(argv[2]) + ".writes");
        if (!out || !pins || !writes)
            throw std::runtime_error("cannot create capture");
        VBbcSoundBusTest chip;
        auto edge = [&]() {
            chip.clk = 0;
            chip.eval();
            chip.clk = 1;
            chip.eval();
        };
        chip.reset = 1;
        if (events.empty() || events.front() != std::array<unsigned, 4>{0, 1, 0, 127})
            throw std::runtime_error("fixture must declare Model B reset pins");
        chip.io_enabled = 1;
        chip.io_touch = 0;
        chip.io_data = 127;
        for (unsigned i = 0; i < 16 * BBC_MASTER_MULTIPLIER; i++)
            edge();
        chip.reset = 0;
        size_t next = 1;
        for (unsigned cycle = 0; cycle < cycles; cycle++) {
            chip.io_touch = 0;
            if (next < events.size() && events[next][0] == cycle + 1) {
                auto e = events[next++];
                chip.io_enabled = e[1];
                chip.io_touch = e[2];
                chip.io_data = e[3];
            }
            for (unsigned subcycle = 0; subcycle < BBC_MASTER_MULTIPLIER; subcycle++) {
                chip.clk = 0;
                chip.eval();
                if (chip.io_write)
                    writes << cycle + 1 << " " << unsigned(chip.io_writeData) << "\n";
                chip.clk = 1;
                chip.eval();
            }
            if (bbc_audio_interval_complete(cycle)) {
                out.put(char(chip.io_sample));
                pins.put(char(chip.io_pins));
            }
        }
        if (next != events.size())
            throw std::runtime_error("unexecuted event");
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
