// Real cold controller commands, no assignment to device or CPU internal state.
#include "VCallbackProbe.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
int main(int argc, char **argv) {
    try {
        if (argc != 3)
            throw std::runtime_error("Expected source fixture and clock multiplier");
        const int multiplier = std::stoi(argv[2]);
        if (multiplier != 1 && multiplier != 3)
            throw std::runtime_error("Bad master clock");
        std::ifstream fixture(argv[1]);
        unsigned id, deadline, ack, write, address, value, level, pending, cases = 0;
        while (fixture >> id >> deadline >> ack >> write >> address >> value >> level >> pending) {
            VCallbackProbe v;
            v.rd = 0;
            v.wr = 0;
            v.address = 0;
            v.data = 0;
            v.base = 0;
            v.reset = 1;
            auto edge = [&]() {
                v.clk = 0;
                v.eval();
                v.clk = 1;
                v.eval();
            };
            edge();
            edge();
            v.reset = 0;
            const unsigned bytes[] = {0x35, 0x0d, 0, 0, 1, 0x53, 0, 0, 0x21};
            unsigned observed = 0;
            for (unsigned clock = 1; clock <= (deadline + 3) * 16 * multiplier; ++clock) {
                v.base = clock % multiplier == 0;
                v.rd = 0;
                v.wr = 0;
                if (clock % (16 * multiplier) == 0) {
                    const auto cycle = clock / (16 * multiplier);
                    if (cycle <= 9) {
                        v.wr = 1;
                        v.address = cycle == 1 || cycle == 6 ? 0 : 1;
                        v.data = bytes[cycle - 1];
                    }
                    if (ack && cycle == ack) {
                        v.address = address;
                        v.data = 0x5a;
                        v.rd = !write;
                        v.wr = write;
                    }
                }
                v.clk = 0;
                v.eval();
                if (v.rd)
                    observed = v.result;
                v.clk = 1;
                v.eval();
            }
            if (v.pending != pending || v.nmi != level || (ack && !write && observed != value)) {
                std::cout << "CALLBACK_MISMATCH case " << id << " pending " << +v.pending << '/'
                          << pending << " level " << +v.nmi << '/' << level << " read " << observed
                          << '/' << value << '\n';
                return 1;
            }
            ++cases;
        }
        if (!fixture.eof() || cases != 16)
            throw std::runtime_error("Incomplete callback fixture");
        std::cout << "Callback ordering: " << cases << " cold cases; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
