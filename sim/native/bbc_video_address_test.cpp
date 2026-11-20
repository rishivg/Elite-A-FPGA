#include "VBbcVideoAddress.h"
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    std::ifstream f(argv[1], std::ios::binary);
    std::vector<unsigned char> corpus{std::istreambuf_iterator<char>(f), {}};
    constexpr unsigned plane = 4194304;
    if (corpus.size() != 2 * plane)
        return 2;
    VBbcVideoAddress v;
    unsigned cases = 0, differences = 0;
    for (unsigned flags = 0; flags < 8; flags++)
        for (unsigned scroll = 0; scroll < 4; scroll++)
            for (unsigned ma = 0; ma < 16384; ma++)
                for (unsigned ra = 0; ra < 8; ra++) {
                    v.io_ma = ma;
                    v.io_raster = ra;
                    v.io_scroll = scroll;
                    v.io_fast = flags & 1;
                    v.io_teletext = (flags >> 1) & 1;
                    v.io_oddTick = (flags >> 2) & 1;
                    v.eval();
                    const unsigned expected =
                        corpus[cases] | (unsigned(corpus[cases + plane]) << 8);
                    if (v.io_address != expected) {
                        if (differences++ < 8)
                            std::cerr << flags << "," << scroll << "," << ma << "," << ra << ": "
                                      << unsigned(v.io_address) << "/" << expected << "\n";
                    }
                    cases++;
                }
    std::cout << "Video address: " << cases << " addresses; " << differences << " differences\n";
    return differences ? 1 : 0;
}
