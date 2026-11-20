#include "VTeletextGlyphRow.h"
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
            throw std::runtime_error("usage: glyph probe FIXTURE");
        std::ifstream fixture(argv[1], std::ios::binary);
        std::array<unsigned char, 12> data{};
        if (!fixture.read(reinterpret_cast<char *>(data.data()), 12) ||
            std::string(reinterpret_cast<char *>(data.data()), 7) != "BBCGLYP" || data[7] != 1 ||
            word(data.data() + 8) != 12288)
            throw std::runtime_error("Invalid glyph header");
        VTeletextGlyphRow chip;
        unsigned differences = 0;
        for (unsigned i = 0; i < 12288; i++) {
            if (!fixture.read(reinterpret_cast<char *>(data.data()), 8))
                throw std::runtime_error("Incomplete glyph fixture");
            const uint32_t input = word(data.data()), expected = word(data.data() + 4);
            if (input >> 26 || ((input >> 7) & 3) >= 3)
                throw std::runtime_error("Invalid glyph input");
            chip.io_character = input & 127;
            chip.io_glyphSet = (input >> 7) & 3;
            chip.io_raster = (input >> 9) & 31;
            chip.io_alpha = (input >> 14) & 63;
            chip.io_neighbour = (input >> 20) & 63;
            chip.eval();
            if (chip.io_weights != expected) {
                if (differences++ < 12)
                    std::cerr << "observation " << i << " char " << unsigned(chip.io_character)
                              << " set " << unsigned(chip.io_glyphSet) << " raster "
                              << unsigned(chip.io_raster) << ": " << chip.io_weights << "/"
                              << expected << "\n";
            }
        }
        if (fixture.peek() != std::char_traits<char>::eof())
            throw std::runtime_error("Trailing glyph fixture data");
        std::cout << "Teletext glyphs: 12288 observations; " << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
