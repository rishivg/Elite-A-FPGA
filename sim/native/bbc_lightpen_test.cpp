#include "VBbcLightPenProbe.h"
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
            throw std::runtime_error("usage: light-pen probe FIXTURE");
        std::ifstream f(argv[1], std::ios::binary);
        std::array<unsigned char, 20> b{};
        if (!f.read(reinterpret_cast<char *>(b.data()), 12) ||
            std::string(reinterpret_cast<char *>(b.data()), 7) != "BBCLPEN" || b[7] != 3 ||
            word(b.data() + 8) != 1536)
            throw std::runtime_error("Invalid light-pen header");
        uint64_t observations = 0, differences = 0;
        for (unsigned id = 0; id < 1536; id++) {
            if (!f.read(reinterpret_cast<char *>(b.data()), 8) || word(b.data()) != id ||
                word(b.data() + 4) != 768)
                throw std::runtime_error("Invalid light-pen case");
            VBbcLightPenProbe c;
            c.tick = 0;
            c.operation = 0;
            c.address = 12;
            c.data = 0;
            c.cb1 = 0;
            c.cb1Pulse = 0;
            c.reset = 1;
            c.clk = 0;
            c.eval();
            c.clk = 1;
            c.eval();
            c.reset = 0;
            for (unsigned i = 0; i < 768; i++) {
                if (!f.read(reinterpret_cast<char *>(b.data()), 20))
                    throw std::runtime_error("Incomplete light-pen corpus");
                const unsigned input = word(b.data()), expected = word(b.data() + 4),
                               pins = word(b.data() + 8), expectedMa = word(b.data() + 12),
                               expectedState = word(b.data() + 16);
                if (input >> 16 || expected >> 14 || pins >> 10)
                    throw std::runtime_error("Invalid light-pen record");
                for (unsigned phase = 0; phase < 16; phase++) {
                    c.tick = phase == 15 && (i % 2 == 0);
                    c.operation = phase == 15 ? (input & 3) : 0;
                    c.address = (input >> 2) & 15;
                    c.data = (input >> 6) & 255;
                    if (phase == 15)
                        c.cb1 = (input >> 14) & 1;
                    c.cb1Pulse = phase == 15 && ((input >> 15) & 1);
                    c.clk = 0;
                    c.eval();
                    c.clk = 1;
                    c.eval();
                }
                c.operation = 0;
                c.tick = 0;
                c.cb1Pulse = 0;
                c.address = 12;
                c.eval();
                const unsigned actual =
                    ((c.registers[4] >> 8) & 255) | ((c.registers[4] & 63) << 8);
                const unsigned actualPins =
                    c.pcr | (unsigned(c.cb2Pin && c.cb2Output) << 8) | (unsigned(c.cb2Output) << 9);
                if (actual != expected || actualPins != pins || c.ma != expectedMa ||
                    c.crtcState != expectedState) {
                    if (differences++ < 16)
                        std::cerr << "case " << id << " clock " << i << ": LP " << actual << "/"
                                  << expected << ", MA " << unsigned(c.ma) << "/" << expectedMa
                                  << ", state " << unsigned(c.crtcState) << "/" << expectedState
                                  << ", regs3 " << c.registers[3] << ", pins " << actualPins << "/"
                                  << pins << "\n";
                }
                observations++;
            }
        }
        if (f.peek() != std::char_traits<char>::eof())
            throw std::runtime_error("Trailing light-pen corpus");
        std::cout << "Light pen: " << observations << " observations in 1536 cases; " << differences
                  << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
