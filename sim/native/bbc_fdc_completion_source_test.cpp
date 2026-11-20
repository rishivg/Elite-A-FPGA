#include "VFdcCompletionSourceProbe.h"
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
        VFdcCompletionSourceProbe v;
        std::ifstream in(std::string(argv[1]) + "/original.bin", std::ios::binary);
        if (!in)
            throw std::runtime_error("Missing completion table");
        std::array<unsigned char, 6> row;
        uint32_t count = 0;
        for (unsigned ready = 0; ready < 2; ready++)
            for (unsigned n = 0; n < 256; n++)
                for (unsigned sector = 0; sector < 256; sector++)
                    for (unsigned step = 0; step < 256; step++) {
                        if (!in.read(reinterpret_cast<char *>(row.data()), row.size()))
                            throw std::runtime_error("Truncated completion table");
                        v.ready = ready;
                        v.countByte = n;
                        v.sectorByte = sector;
                        v.stepByte = step;
                        v.parameterByte = 0;
                        v.eval();
                        if (v.followingCount != row[0] || v.followingSector != row[1] ||
                            v.driveMask != row[2] || v.finishResult != row[3] ||
                            v.callOrder != (unsigned(row[4]) | (unsigned(row[5]) << 8))) {
                            std::cout << "COMPLETION_SOURCE_MISMATCH record " << count << '\n';
                            return 1;
                        }
                        ++count;
                    }
        char extra;
        if (in.get(extra) || !in.eof())
            throw std::runtime_error("Overlong completion table");
        std::ifstream spec(std::string(argv[1]) + "/specify.bin", std::ios::binary);
        if (!spec)
            throw std::runtime_error("Missing SPECIFY table");
        std::array<unsigned char, 3> sr;
        for (unsigned p = 0; p < 256; p++) {
            if (!spec.read(reinterpret_cast<char *>(sr.data()), sr.size()))
                throw std::runtime_error("Truncated SPECIFY table");
            v.parameterByte = p;
            v.eval();
            if (v.pointer != sr[0] || v.parameterCount != sr[1] || v.callback != sr[2]) {
                std::cout << "SPECIFY_SOURCE_MISMATCH parameter " << p << '\n';
                return 1;
            }
        }
        if (spec.get(extra) || !spec.eof())
            throw std::runtime_error("Overlong SPECIFY table");
        std::cout
            << "Original completion: 33554432 decisions; 256 SPECIFY dispatches; 0 differences\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
