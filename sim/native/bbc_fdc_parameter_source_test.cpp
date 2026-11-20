#include "VFdcParameterSource.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected original parameter fixtures");
        std::ifstream input(argv[1]);
        if (!input)
            throw std::runtime_error("Missing original parameter fixtures");
        VFdcParameterSource v;
        unsigned pointer, count, data, callback, nextPointer, nextCount, started, finished,
            rows = 0;
        while (input >> pointer >> count >> data >> callback >> nextPointer >> nextCount >>
               started >> finished) {
            for (unsigned i = 0; i < 8; ++i)
                v.registers[i] = 0;
            v.registers[0] = pointer | (count << 8);
            v.param = data;
            v.callback = callback;
            v.eval();
            if (v.trap || (v.following[0] & 65535) != (nextPointer | (nextCount << 8)) ||
                v.started != started || v.finished != finished) {
                std::cout << "PARAMETER_SOURCE_MISMATCH " << rows << '\n';
                return 1;
            }
            ++rows;
        }
        if (!input.eof() || rows != 524288)
            throw std::runtime_error("Incomplete original parameter fixtures");
        std::cout << "Original parameter acceptance: " << rows << " fixtures; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
