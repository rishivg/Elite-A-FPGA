// Actual BBC PSG and the production recorder's interval boundary, without CPU
// or game code. The independent original SoundChip provides expected bytes.
#include "VPsg.h"
#include "bbc_audio.h"
#include <array>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <vector>

int main(int argc, char **argv) {
    try {
        if (argc != 2 && argc != 4)
            throw std::runtime_error("usage: bbc_audio_test OUTPUT [WRITES CYCLES]");
        std::ofstream out(argv[1], std::ios::binary),
            pins(std::string(argv[1]) + ".pins", std::ios::binary);
        if (!out)
            throw std::runtime_error("cannot create capture");
        VPsg chip;
        // The generated PSG belongs to BbcAudio's enable domain. This
        // standalone 32 MHz probe accepts every physical master edge.
        chip.audio_newClockEnable = 1;
        auto edge = [&]() {
            chip.clk = 0;
            chip.eval();
            chip.clk = 1;
            chip.eval();
        };
        chip.reset = 1;
        chip.io_write = 0;
        chip.io_data = 0;
        for (int i = 0; i < 16; i++)
            edge();
        chip.reset = 0;
        // Mute while the zero-period tone settles, then change volume before,
        // at and after capture boundaries. Counter phase cannot affect these
        // observations: the first high tone lasts longer than this fixture.
        std::vector<std::array<unsigned, 2>> writes{{0, 0x80},   {64, 0},     {127, 0x90},
                                                    {256, 0x9f}, {289, 0x90}, {417, 0x9f},
                                                    {511, 0x90}, {640, 0x9f}};
        unsigned cycles = 1024;
        if (argc == 4) {
            cycles = std::stoul(argv[3]);
            writes.clear();
            std::ifstream input(argv[2]);
            if (!input)
                throw std::runtime_error("Missing write fixture");
            unsigned cycle, byte;
            while (input >> cycle >> byte) {
                if (cycle >= cycles || byte > 255 || (!writes.empty() && cycle <= writes.back()[0]))
                    throw std::runtime_error("Invalid write fixture");
                writes.push_back({cycle, byte});
            }
            if (!input.eof())
                throw std::runtime_error("Malformed write fixture");
        }
        size_t next = 0;
        for (unsigned cycle = 0; cycle < cycles; cycle++) {
            chip.io_write = next < writes.size() && cycle == writes[next][0];
            if (chip.io_write)
                chip.io_data = writes[next++][1];
            edge();
            if (bbc_audio_interval_complete(cycle)) {
                out.put(char(chip.io_sample));
                pins.put(char(chip.io_tone | (chip.io_noise << 3)));
            }
        }
        if (next != writes.size())
            throw std::runtime_error("fixture did not execute all writes");
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
