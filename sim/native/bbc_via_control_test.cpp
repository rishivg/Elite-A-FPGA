#include "VVia6522.h"
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
            throw std::runtime_error("usage: VIA control probe FIXTURE");
        std::ifstream f(argv[1], std::ios::binary);
        std::array<unsigned char, 12> b{};
        if (!f.read(reinterpret_cast<char *>(b.data()), 12) ||
            std::string(reinterpret_cast<char *>(b.data()), 7) != "VIACTRL" || b[7] != 2 ||
            word(b.data() + 8) != 1024)
            throw std::runtime_error("Invalid VIA control header");
        uint64_t observations = 0, differences = 0;
        for (unsigned id = 0; id < 1024; id++) {
            if (!f.read(reinterpret_cast<char *>(b.data()), 8) || word(b.data()) != id)
                throw std::runtime_error("Invalid VIA control case");
            const unsigned count = word(b.data() + 4);
            if (count < 80 || count > 1024)
                throw std::runtime_error("Invalid VIA control count");
            VVia6522 c;
            c.io_tick = c.io_phi2 = 0;
            c.io_write = 0;
            c.io_address = 0;
            c.io_data = 0;
            c.io_ca1 = 0;
            c.io_cb1 = 0;
            c.io_cb1Pulse = 0;
            c.io_read = 0;
            c.io_ca2 = 0;
            c.io_ca2AfterWrite = 0;
            c.io_cb2 = 0;
            c.io_portAIn = 255;
            c.io_portBIn = 255;
            c.reset = 1;
            c.clk = 0;
            c.eval();
            c.clk = 1;
            c.eval();
            c.reset = 0;
            for (unsigned i = 0; i < count; i++) {
                if (!f.read(reinterpret_cast<char *>(b.data()), 8))
                    throw std::runtime_error("Incomplete VIA control fixture");
                const auto input = word(b.data()), expected = word(b.data() + 4);
                if (input >> 17 || expected >> 16)
                    throw std::runtime_error("Invalid VIA control input");
                c.io_write = input & 1;
                c.io_address = (input >> 1) & 15;
                c.io_data = (input >> 5) & 255;
                c.io_ca1 = (input >> 13) & 1;
                c.io_cb1 = (input >> 14) & 1;
                c.io_tick = (input >> 15) & 1;
                c.io_phi2 = c.io_tick;
                c.io_cb1Pulse = (input >> 16) & 1;
                c.clk = 0;
                c.eval();
                const unsigned edges =
                    (unsigned(c.io_ca2Rise) << 12) | (unsigned(c.io_cb2Rise) << 13) |
                    (unsigned(c.io_ca2Fall) << 14) | (unsigned(c.io_cb2Fall) << 15);
                c.clk = 1;
                c.eval();
                c.io_write = 0;
                c.io_address = 12;
                c.eval();
                const unsigned actual =
                    c.io_result | (unsigned(c.io_ca2Pin && c.io_ca2Output) << 8) |
                    (unsigned(c.io_cb2Pin && c.io_cb2Output) << 9) |
                    (unsigned(c.io_ca2Output) << 10) | (unsigned(c.io_cb2Output) << 11) | edges;
                if (actual != expected) {
                    if (differences++ < 16)
                        std::cerr << "case " << id << " clock " << i << ": input " << input
                                  << " observed " << actual << "/" << expected << "\n";
                }
                observations++;
            }
        }
        if (f.peek() != std::char_traits<char>::eof())
            throw std::runtime_error("Trailing VIA control data");
        std::cout << "VIA control pins: " << observations << " observations in 1024 cases; "
                  << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
