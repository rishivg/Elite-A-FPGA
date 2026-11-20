#include "VSaa5050Cell.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

static uint32_t word(const unsigned char *p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("usage: teletext probe FIXTURE");
        std::ifstream fixture(argv[1], std::ios::binary);
        std::array<unsigned char, 16> data{};
        if (!fixture.read(reinterpret_cast<char *>(data.data()), 12) ||
            std::string(reinterpret_cast<char *>(data.data()), 7) != "BBCCELL" || data[7] != 1 ||
            word(data.data() + 8) != 50)
            throw std::runtime_error("Invalid teletext header");
        uint64_t observations = 0, differences = 0;
        for (unsigned id = 0; id < 50; id++) {
            if (!fixture.read(reinterpret_cast<char *>(data.data()), 8) || word(data.data()) != id)
                throw std::runtime_error("Invalid teletext case");
            const unsigned count = word(data.data() + 4);
            if (!count || count > 250000)
                throw std::runtime_error("Invalid observation count");
            VSaa5050Cell chip;
            chip.io_enable = 0;
            chip.io_operation = 0;
            chip.io_data = 0;
            auto edge = [&]() {
                chip.clk = 0;
                chip.eval();
                chip.clk = 1;
                chip.eval();
            };
            chip.reset = 1;
            edge();
            edge();
            chip.reset = 0;
            for (unsigned i = 0; i < count; i++) {
                if (!fixture.read(reinterpret_cast<char *>(data.data()), 16))
                    throw std::runtime_error("Incomplete teletext fixture");
                const auto input = word(data.data()), expectedCell = word(data.data() + 4),
                           expectedState = word(data.data() + 8);
                if (input >> 17 || (input & 255) > 5 || expectedCell >> 21 || expectedState >> 31)
                    throw std::runtime_error("Invalid teletext record");
                chip.io_operation = input & 255;
                chip.io_data = (input >> 8) & 255;
                chip.io_enable = (input >> 16) & 1;
                edge();
                chip.eval();
                const uint32_t colours = chip.io_foreground | (uint32_t(chip.io_background) << 3);
                const uint32_t expectedColours = (expectedCell >> 14) & 63;
                const uint32_t expectedWeights = word(data.data() + 12);
                if (colours != expectedColours || chip.io_weights != expectedWeights) {
                    if (differences++ < 16)
                        std::cerr << "case " << id << " observation " << i << " operation "
                                  << (input & 255) << " data " << ((input >> 8) & 255) << ": cell "
                                  << colours << "/" << expectedColours << " weights "
                                  << chip.io_weights << "/" << expectedWeights << "\n";
                }
                observations++;
            }
        }
        if (fixture.peek() != std::char_traits<char>::eof())
            throw std::runtime_error("Trailing teletext fixture data");
        std::cout << "Teletext cells: " << observations << " observations in 50 cases; "
                  << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
