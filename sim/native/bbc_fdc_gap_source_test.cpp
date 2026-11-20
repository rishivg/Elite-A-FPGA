#include "VFdcGapSourceProbe.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected table directory");
        VFdcGapSourceProbe v;
        std::ifstream in(std::string(argv[1]) + "/original.bin", std::ios::binary);
        if (!in)
            throw std::runtime_error("Missing gap table");
        std::array<unsigned char, 7> row;
        unsigned count = 0;
        for (unsigned gap = 0; gap < 256; gap++)
            for (unsigned context = 0; context < 256; context++) {
                if (!in.read(reinterpret_cast<char *>(row.data()), row.size()))
                    throw std::runtime_error("Truncated gap table");
                if (row[1] > 1 || row[3] > 1 || row[6] > 1)
                    throw std::runtime_error("Nonboolean gap decision");
                v.gapByte = gap;
                v.contextByte = context;
                v.value = 0;
                v.eval();
                if (v.followingGap != row[0] || v.stateWritten != row[1] || v.nextState != row[2] ||
                    v.writeRun != row[3] || v.writeContext != row[4] || v.writeValue != row[5] ||
                    v.trap != row[6]) {
                    std::cout << "GAP_SOURCE_MISMATCH record " << count << '\n';
                    return 1;
                }
                ++count;
            }
        char extra;
        if (in.get(extra) || !in.eof())
            throw std::runtime_error("Overlong gap table");
        std::ifstream wr(std::string(argv[1]) + "/writes.bin", std::ios::binary);
        if (!wr)
            throw std::runtime_error("Missing gap write table");
        for (unsigned value = 0; value < 256; value++) {
            char byte;
            if (!wr.get(byte))
                throw std::runtime_error("Truncated gap write table");
            v.value = value;
            v.eval();
            if (v.writtenGap != uint8_t(byte)) {
                std::cout << "GAP_WRITE_SOURCE_MISMATCH value " << value << '\n';
                return 1;
            }
        }
        if (wr.get(extra) || !wr.eof())
            throw std::runtime_error("Overlong gap write table");
        std::cout << "Original gap: 65536 decisions; 256 writes; 0 differences\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
