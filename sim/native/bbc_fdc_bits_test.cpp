#include "VFdcBitProbe.h"
#include "bbc_fm_media.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
std::vector<uint8_t> read(const std::string &path, size_t bytes) {
    std::ifstream input(path, std::ios::binary);
    std::vector<uint8_t> data{std::istreambuf_iterator<char>(input), {}};
    if (!input || data.size() != bytes)
        throw std::runtime_error("Incomplete source truth table");
    return data;
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected source truth-table directory");
        const std::string root = argv[1];
        const auto crc = read(root + "/crc.bin", 65536 * 256 * 2),
                   fm = read(root + "/fm.bin", 65536 * 4),
                   bits = read(root + "/extract.bin", 65536);
        VFdcBitProbe v;
        v.clocks = 0;
        v.bits = 0;
        for (unsigned initial = 0; initial < 65536; initial++)
            for (unsigned byte = 0; byte < 256; byte++) {
                v.crc = initial;
                v.data = byte;
                v.eval();
                const size_t p = (initial * 256 + byte) * 2;
                const unsigned expected = crc[p] | (unsigned(crc[p + 1]) << 8);
                if (bbc_fm::crc_byte(initial, byte) != expected) {
                    std::cout << "MEDIA_CRC_MISMATCH " << initial << ' ' << byte << '\n';
                    return 1;
                }
                if (v.crcValue != expected) {
                    std::cout << "CRC_MISMATCH " << initial << ' ' << byte << '\n';
                    return 1;
                }
            }
        for (unsigned word = 0; word < 65536; word++) {
            v.clocks = word >> 8;
            v.data = word & 255;
            v.eval();
            const size_t p = word * 4;
            const uint32_t expected = uint32_t(fm[p]) | (uint32_t(fm[p + 1]) << 8) |
                                      (uint32_t(fm[p + 2]) << 16) | (uint32_t(fm[p + 3]) << 24);
            if (v.pulses != expected) {
                std::cout << "FM_MISMATCH " << word << '\n';
                return 1;
            }
            for (const uint32_t high : {0u, 0x10000u, 0x80000000u, 0xffff0000u}) {
                v.bits = word | high;
                v.eval();
                if (v.extracted != bits[word]) {
                    std::cout << "EXTRACT_MISMATCH " << word << ' ' << high << '\n';
                    return 1;
                }
            }
        }
        std::cout << "Original bit kernels: 16777216 CRC, 65536 FM, 65536x4 extraction cases; 0 "
                     "differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
