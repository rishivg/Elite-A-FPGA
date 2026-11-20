#include "VCrtc6845.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static uint32_t word(const unsigned char *p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
int main(int argc, char **argv) {
    try {
        if (argc < 2 || argc > 3 || (argc == 3 && std::string(argv[2]) != "dense"))
            throw std::runtime_error("usage: CRTC probe FIXTURE [dense]");
        const bool dense = argc == 3;
        std::ifstream f(argv[1], std::ios::binary);
        std::array<unsigned char, 12> header{};
        if (!f.read(reinterpret_cast<char *>(header.data()), header.size()) ||
            std::string(reinterpret_cast<char *>(header.data()), 7) != "BBC6845" ||
            header[7] != 1 || word(header.data() + 8) != 162)
            throw std::runtime_error("Invalid CRTC fixture header");
        constexpr unsigned count = 40276;
        std::vector<unsigned char> payload(count * 12);
        const unsigned masks[] = {255, 255, 255, 255, 127, 31, 127, 127, 243,
                                  31,  127, 31,  63,  255, 63, 255, 63,  255};
        uint64_t differences = 0, observations = 0, edges = 0;
        for (unsigned id = 0; id < 162; id++) {
            if (!f.read(reinterpret_cast<char *>(header.data()), header.size()) ||
                word(header.data()) != id || word(header.data() + 4) != id / 81 ||
                word(header.data() + 8) != count ||
                !f.read(reinterpret_cast<char *>(payload.data()), payload.size()))
                throw std::runtime_error("Incomplete CRTC fixture case");
            const bool fast = id >= 81;
            VCrtc6845 c;
            c.io_enable = 0;
            for (unsigned i = 0; i < 5; i++)
                c.io_registers[i] = 0;
            auto edge = [&]() {
                c.clk = 0;
                c.eval();
                c.clk = 1;
                c.eval();
                edges++;
            };
            c.reset = 1;
            edge();
            edge();
            c.reset = 0;
            unsigned reg = 0;
            for (unsigned i = 0; i < count; i++) {
                const auto p = payload.data() + 12 * i;
                const uint32_t input = word(p), state = word(p + 4), address = word(p + 8);
                const unsigned operation = input & 255, busAddress = (input >> 8) & 255,
                               data = (input >> 16) & 255;
                if (input >> 24 || operation > 1 || busAddress > 1 || state >> 24 ||
                    address >= 16384)
                    throw std::runtime_error("Invalid CRTC observation");
                unsigned endpointAddress = 0;
                if (dense) {
                    for (unsigned phase = 0; phase < 16; phase++) {
                        c.io_enable = phase == 15 && (fast || ((i + 1) & 1));
                        c.clk = 0;
                        c.eval();
                        endpointAddress = c.io_busAddress;
                        edge();
                    }
                } else {
                    c.io_enable = fast || ((i + 1) & 1);
                    c.clk = 0;
                    c.eval();
                    endpointAddress = c.io_busAddress;
                    edge();
                }
                if (operation) {
                    if (!busAddress) {
                        reg = data & 31;
                    } else {
                        if (reg >= 18)
                            throw std::runtime_error("Invalid fixture register");
                        const unsigned shift = (reg % 4) * 8;
                        auto &bank = c.io_registers[reg / 4];
                        bank = (bank & ~(uint32_t(255) << shift)) |
                               (uint32_t(data & masks[reg]) << shift);
                    }
                }
                c.io_enable = 0;
                c.eval();
                const uint32_t actual =
                    c.io_horizontal | (uint32_t(c.io_row) << 8) | (uint32_t(c.io_raster) << 15) |
                    (uint32_t(c.io_vsync) << 20) | (uint32_t(c.io_hsync) << 21) |
                    (uint32_t(c.io_displayActive) << 22) | (uint32_t(c.io_rasterPins & 1) << 23);
                if (actual != state || c.io_address != address || endpointAddress != address) {
                    if (differences++ < 12)
                        std::cerr << "case " << id << " clock " << i + 1 << ": state " << actual
                                  << "/" << state << ", MA " << unsigned(c.io_address) << "/"
                                  << address << "\n";
                }
                observations++;
            }
        }
        if (f.peek() != std::char_traits<char>::eof() || observations != 6524712)
            throw std::runtime_error("Trailing or incomplete CRTC observations");
        std::cout << "CRTC " << (dense ? "dense" : "enabled") << ": " << observations
                  << " observations in 162 cases; " << differences << " differences; " << edges
                  << " clock edges\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
