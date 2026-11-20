// Compare the lowered source projection with independently executed JS fixtures.
#include "VCrtcSourceNext.h"
#include <fstream>
#include <iostream>
#include <string>

template <size_t N> static void unpack(const std::string &hex, VlWide<N> &word) {
    for (size_t i = 0; i < N; ++i) {
        const size_t end = hex.size() > i * 8 ? hex.size() - i * 8 : 0;
        const size_t start = end > 8 ? end - 8 : 0;
        word[i] = end ? std::stoul(hex.substr(start, end - start), nullptr, 16) : 0;
    }
}

int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    std::ifstream input(argv[1]);
    if (!input)
        return 2;
    VCrtcSourceNext model;
    unsigned count = 0;
    std::string state, registers, expected;
    while (input >> state >> registers >> expected) {
        unpack(state, model.state);
        unpack(registers, model.registers);
        VlWide<3> result;
        unpack(expected, result);
        model.eval();
        for (unsigned i = 0; i < 3; ++i)
            if (model.nextState[i] != result[i]) {
                std::cerr << "Source projection differs at fixture " << count << " state " << state
                          << " registers " << registers << " expected " << expected << " word " << i
                          << " actual " << std::hex << model.nextState[i] << " expected "
                          << result[i] << "\n";
                return 1;
            }
        ++count;
    }
    if (!input.eof() || count != 65536)
        return 2;
    std::cout << "Original CRTC projection: " << count << " fixtures; 0 differences\n";
    return 0;
}
