#include "VFdcReadySourceProbe.h"
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
        VFdcReadySourceProbe v;
        std::ifstream in(std::string(argv[1]) + "/original.bin", std::ios::binary);
        if (!in)
            throw std::runtime_error("Missing ready table");
        std::array<unsigned char, 6> row;
        uint32_t count = 0;
        for (unsigned pins = 0; pins < 256; pins++)
            for (unsigned drive = 0; drive < 256; drive++)
                for (unsigned latch = 0; latch < 256; latch++) {
                    if (!in.read(reinterpret_cast<char *>(row.data()), row.size()))
                        throw std::runtime_error("Truncated ready table");
                    if (row[2] > 1 || row[3] > 1)
                        throw std::runtime_error("Nonboolean ready decision");
                    v.pinsByte = pins;
                    v.driveByte = drive;
                    v.oldLatch = latch;
                    v.value = 0;
                    v.eval();
                    if (v.followingCopy != row[0] || v.followingLatch != row[1] ||
                        v.ready != row[2] || v.finished != row[3] || v.finishResult != row[4] ||
                        v.statusValue != row[5]) {
                        std::cout << "READY_SOURCE_MISMATCH record " << count << '\n';
                        return 1;
                    }
                    ++count;
                }
        char extra;
        if (in.get(extra) || !in.eof())
            throw std::runtime_error("Overlong ready table");
        std::ifstream writes(std::string(argv[1]) + "/writes.bin", std::ios::binary);
        if (!writes)
            throw std::runtime_error("Missing latch-write table");
        for (unsigned value = 0; value < 256; value++) {
            char byte;
            if (!writes.get(byte))
                throw std::runtime_error("Truncated latch-write table");
            v.value = value;
            v.eval();
            if (v.writtenLatch != uint8_t(byte)) {
                std::cout << "READY_WRITE_SOURCE_MISMATCH value " << value << '\n';
                return 1;
            }
        }
        if (writes.get(extra) || !writes.eof())
            throw std::runtime_error("Overlong latch-write table");
        std::cout << "Original READY: 16777216 decisions; 256 latch writes; 0 differences\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
