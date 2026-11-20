#include "VWriteGapSourceProbe.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
using Row = std::array<unsigned, 7>;
static std::vector<uint8_t> read(const std::string &p, size_t size) {
    std::ifstream f(p, std::ios::binary);
    std::vector<uint8_t> b((std::istreambuf_iterator<char>(f)), {});
    if (b.size() != size)
        throw std::runtime_error("Incorrect source table length");
    return b;
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            return 2;
        std::string d = argv[1];
        auto init = read(d + "/init.bin", 256 * 7);
        auto run = read(d + "/run.bin", 66304 * 7);
        VWriteGapSourceProbe m;
        size_t p = 0;
        for (unsigned v = 0; v < 256; v++) {
            m.value = v;
            m.contextByte = 5;
            m.eval();
            Row actual = {m.initRunData, m.dataByte,   m.clocksByte, m.nextContext,
                          m.initState,   m.gateRaised, m.initCrc};
            for (auto x : actual)
                if (x != init[p++]) {
                    std::cout << "WRITE_RUN_INIT_MISMATCH value " << v << "\n";
                    return 1;
                }
        }
        p = 0;
        unsigned record = 0;
        auto test = [&](unsigned l, unsigned h, unsigned s, unsigned v) {
            m.low = l;
            m.high = h;
            m.status = s;
            m.runData = v;
            m.eval();
            Row actual = {m.nextLow,      m.nextHigh,  m.nextStatus, m.nextData,
                          m.stateWritten, m.nextState, m.crcCalled};
            for (auto x : actual)
                if (x != run[p++])
                    return false;
            record++;
            return true;
        };
        for (unsigned l = 0; l < 256; l++)
            for (unsigned h = 0; h < 256; h++)
                if (!test(l, h, 0, 85)) {
                    std::cout << "WRITE_RUN_SOURCE_MISMATCH record " << record << "\n";
                    return 1;
                }
        for (unsigned v = 0; v < 256; v++)
            if (!test(2, 0, 0, v)) {
                std::cout << "WRITE_RUN_SOURCE_MISMATCH record " << record << "\n";
                return 1;
            }
        for (unsigned s = 0; s < 256; s++)
            for (unsigned l : {1u, 2u})
                if (!test(l, 0, s, 85)) {
                    std::cout << "WRITE_RUN_SOURCE_MISMATCH record " << record << "\n";
                    return 1;
                }
        std::cout << "Original write run: 66304 decisions; 256 initializers; 0 differences\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
