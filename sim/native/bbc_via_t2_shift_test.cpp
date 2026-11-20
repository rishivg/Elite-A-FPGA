#include "VVia6522.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
uint32_t word(const unsigned char *p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("usage: VIA T2 probe FIXTURE");
        std::ifstream fixture(argv[1], std::ios::binary);
        std::array<unsigned char, 12> header{};
        if (!fixture.read(reinterpret_cast<char *>(header.data()), header.size()) ||
            std::string(reinterpret_cast<char *>(header.data()), 4) != "T2S1" ||
            word(header.data() + 4) != 1536)
            throw std::runtime_error("invalid T2 corpus");
        unsigned observations = 0, differences = 0;
        for (unsigned id = 0; id < 1536; id++) {
            std::array<unsigned char, 8> record{};
            if (!fixture.read(reinterpret_cast<char *>(record.data()), record.size()) ||
                word(record.data()) != id)
                throw std::runtime_error("invalid T2 case");
            const unsigned length = word(record.data() + 4);
            if (length < 8 || length > 10000)
                throw std::runtime_error("invalid T2 case length");
            VVia6522 via;
            via.io_tick = via.io_read = via.io_write = 0;
            via.io_phi2 = 0;
            via.io_address = via.io_data = 0;
            via.io_portAIn = via.io_portBIn = 255;
            via.io_ca1 = via.io_ca2 = via.io_ca2AfterWrite = via.io_cb1 = via.io_cb1Pulse =
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
            auto peek = [&](unsigned address) {
                via.io_address = address;
                via.eval();
                return unsigned(via.io_result);
            };
            for (unsigned i = 0; i < length; i++) {
                if (!fixture.read(reinterpret_cast<char *>(record.data()), record.size()) ||
                    record[0] > 2 || record[1] > 15 || record[3])
                    throw std::runtime_error("invalid T2 operation");
                via.io_read = via.io_write = via.io_tick = 0;
                for (unsigned phase = 0; phase < 31; phase++) {
                    via.io_phi2 = phase >= 16;
                    edge();
                }
                via.io_tick = 1;
                via.io_phi2 = 1;
                via.io_read = record[0] == 2;
                via.io_write = record[0] == 1;
                via.io_address = record[1];
                via.io_data = record[2];
                via.clk = 0;
                via.eval();
                const unsigned read = via.io_read ? via.io_result : 0;
                via.clk = 1;
                via.eval();
                via.io_tick = via.io_read = via.io_write = 0;
                const unsigned count = peek(8) | (peek(9) << 8), flags = peek(13);
                const unsigned expectedCount = record[4] | (record[5] << 8);
                if (count != expectedCount || flags != record[6] || read != record[7]) {
                    if (differences++ < 20)
                        std::cerr << "case " << id << " op " << i << " " << unsigned(record[0])
                                  << "/" << unsigned(record[1]) << ": count " << count << "/"
                                  << expectedCount << " flags " << flags << "/"
                                  << unsigned(record[6]) << " read " << read << "/"
                                  << unsigned(record[7]) << "\n";
                }
                observations++;
            }
        }
        if (observations != word(header.data() + 8) ||
            fixture.peek() != std::ifstream::traits_type::eof())
            throw std::runtime_error("incomplete T2 corpus");
        std::cout << "VIA T2 shift counter: " << observations << " observations in 1536 cases; "
                  << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
