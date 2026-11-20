#include "VBbcVideo.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input)
        return 2;
    std::vector<unsigned char> gold((std::istreambuf_iterator<char>(input)), {});
    if (gold.size() != 16384)
        return 2;
    std::ofstream out(argv[2]);
    if (!out)
        return 2;
    out << "register data port actual expected\n";
    VBbcVideo v;
    v.io_crtcWrite = 0;
    v.io_lightPenPulse = 0;
    v.io_ulaWrite = 0;
    v.io_address = 0;
    v.io_data = 0;
    v.io_memoryData = 0;
    v.io_scroll = 0;
    auto edge = [&]() {
        v.clk = 0;
        v.eval();
        v.clk = 1;
        v.eval();
    };
    v.reset = 1;
    edge();
    edge();
    v.reset = 0;
    auto write = [&](unsigned addr, unsigned value) {
        v.io_address = addr;
        v.io_data = value;
        v.io_crtcWrite = 1;
        edge();
        v.io_crtcWrite = 0;
        v.eval();
    };
    unsigned differences = 0, observations = 0, dataWrites = 0;
    for (unsigned reg = 0; reg < 32; reg++)
        for (unsigned value = 0; value < 256; value++) {
            write(0, reg);
            write(1, value);
            dataWrites++;
            for (unsigned addr = 0; addr < 2; addr++) {
                v.io_address = addr;
                v.eval();
                unsigned want = gold[observations++];
                if (v.io_result != want) {
                    differences++;
                    out << reg << " " << value << " " << addr << " " << unsigned(v.io_result) << " "
                        << want << "\n";
                }
            }
        }
    out.close();
    if (!out)
        return 2;
    std::cout << "CRTC registers: " << dataWrites << " writes; " << observations << " reads; "
              << differences << " differences\n";
    return differences ? 1 : 0;
}
