#include "VPrefixProbe.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
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
        VPrefixProbe m;
        m.oldData = 90;
        std::string dir = argv[1];
        for (bool writing : {false, true}) {
            auto bytes = read(dir + (writing ? "/writing.bin" : "/reading.bin"),
                              size_t(256) * 256 * (writing ? 1 : 256) * 8);
            size_t p = 0;
            unsigned record = 0;
            m.writingRun = writing;
            for (unsigned status = 0; status < 256; status++)
                for (unsigned command = 0; command < 256; command++)
                    for (unsigned value = 0; value < (writing ? 1u : 256u); value++) {
                        m.status = status;
                        m.command = command;
                        m.byteValue = value;
                        m.eval();
                        std::array<unsigned, 8> got = {
                            m.calledLoss, m.dataWritten, m.followingData, m.raiseMask,
                            m.aborted,    m.finished,    m.finishResult,  m.bodyCalled};
                        for (auto v : got)
                            if (v != bytes[p++]) {
                                std::cout << "PREFIX_SOURCE_MISMATCH "
                                          << (writing ? "writing" : "reading") << " record "
                                          << record << "\n";
                                return 1;
                            }
                        record++;
                    }
        }
        std::cout << "Original prefix: 16777216 reading; 65536 writing; 0 differences\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
