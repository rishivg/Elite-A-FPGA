// Cross-check the lowered original arithmetic against independently executed JS.
#include "VFdcSeekSource.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected original seek fixture");
        std::ifstream input(argv[1]);
        if (!input)
            throw std::runtime_error("Missing original seek fixture");
        VFdcSeekSource v;
        unsigned requested, retry, bank, bad0, bad1, expected, count = 0;
        while (input >> requested >> retry >> bank >> bad0 >> bad1 >> expected) {
            if (requested > 255 || retry > 255 || bank > 1 || bad0 > 255 || bad1 > 255 ||
                expected > 255)
                throw std::runtime_error("Malformed original seek fixture");
            for (unsigned i = 0; i < 8; ++i)
                v.registers[i] = 0;
            auto store = [&](unsigned address, unsigned data) {
                v.registers[address / 4] |= data << ((address % 4) * 8);
            };
            store(7, requested);
            store(1, retry);
            store(bank ? 0x18 : 0x10, bad0);
            store(bank ? 0x19 : 0x11, bad1);
            v.driveOut = bank ? 0x80 : 0x40;
            v.callContext = count;
            v.eval();
            if (v.target != expected || v.unclamped != expected) {
                std::cout << "SEEK_SOURCE_MISMATCH " << count << " " << +v.target << "/" << expected
                          << '\n';
                return 1;
            }
            ++count;
        }
        if (!input.eof() || count != 524288)
            throw std::runtime_error("Incomplete original seek fixture inventory");
        std::cout << "Original seek arithmetic: " << count << " fixtures; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
