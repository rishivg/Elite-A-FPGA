// Frame-driven demo regressions through public Soc ports. The CPU runs the
// unchanged firmware; framebuffer reads and acknowledgement each clock the RTL.
#include "VSoc.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

static void word(uint32_t value) {
    for (int shift = 24; shift >= 0; shift -= 8)
        std::cout.put(char(value >> shift));
}

int main(int argc, char **argv) {
    try {
        Verilated::commandArgs(argc, argv);
        VSoc top;
        top.clk = 0;
        top.reset = 1;
        top.io_input = top.io_input2 = top.io_fbRdAddr = 0;
        top.io_frameAck = 0;
        auto edge = [&]() {
            top.clk = 0;
            top.eval();
            top.clk = 1;
            top.eval();
            if (top.io_trap)
                throw std::runtime_error("demo CPU trapped");
        };
        // Match SpinalSim forkStimulus: reset is held for 32 rising edges.
        for (int i = 0; i < 32; ++i)
            edge();
        top.reset = 0;
        // Spinal waitSampling(128) counts active edges after its reset.
        // Apply the same warm-up before accepting the first frame request.
        for (int i = 0; i < 128; ++i)
            edge();
        std::string line;
        unsigned frames = 0;
        while (std::getline(std::cin, line)) {
            uint64_t input, input2, limit;
            std::istringstream request(line);
            std::string extra;
            if (!(request >> input >> input2 >> limit) || (request >> extra))
                throw std::runtime_error("malformed frame request");
            if (input > UINT32_MAX || input2 > UINT32_MAX || !limit || limit > 5000000)
                throw std::runtime_error("invalid frame request");
            top.io_input = uint32_t(input);
            top.io_input2 = uint32_t(input2);
            uint32_t waited = 0;
            while (!top.io_frameReady && waited < limit) {
                edge();
                ++waited;
            }
            if (!top.io_frameReady)
                throw std::runtime_error("frame presentation timed out");
            word(top.io_dbg);
            word(top.io_audioSample);
            word(waited);
            for (int address = 0; address < 16384; ++address) {
                top.io_fbRdAddr = address;
                edge();
                std::cout.put(char(top.io_fbRdData));
            }
            top.io_frameAck = 1;
            edge();
            top.io_frameAck = 0;
            std::cout.flush();
            if (!std::cout)
                throw std::runtime_error("frame response pipe closed");
            ++frames;
        }
        if (!std::cin.eof() || !frames)
            throw std::runtime_error("malformed frame request");
        top.final();
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "Soc frame capture failed: " << error.what() << '\n';
        return 2;
    }
}
