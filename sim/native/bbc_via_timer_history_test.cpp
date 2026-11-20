// Compare ordinary timer/ACR bus histories against unchanged BBC UserVia.
// Every master edge executes; no device state is supplied by the reference.
#include "VVia6522.h"
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

int main(int argc, char **argv) {
    if (argc != 3)
        return 2;
    const unsigned multiplier = std::stoul(argv[2]);
    if (multiplier != 1 && multiplier != 3)
        return 2;
    const unsigned masterCycles = 32 * multiplier;
    std::ifstream fixture(argv[1]);
    std::string line;
    unsigned caseId = 0, cases = 0, operations = 0, differences = 0;
    VVia6522 via;
    via.io_tick = via.io_phi2 = via.io_read = via.io_write = 0;
    via.io_address = via.io_data = 0;
    via.io_portAIn = via.io_portBIn = 255;
    via.io_ca1 = via.io_ca2 = via.io_cb1 = via.io_cb2 = 0;
    via.io_ca2AfterWrite = via.io_cb1Pulse = 0;
    auto edge = [&]() {
        via.clk = 0;
        via.eval();
        via.clk = 1;
        via.eval();
    };
    while (std::getline(fixture, line)) {
        if (line.rfind("case ", 0) == 0) {
            caseId = std::stoul(line.substr(5));
            if (caseId != cases++)
                return 2;
            via.reset = 1;
            via.io_tick = via.io_read = via.io_write = via.io_phi2 = 0;
            for (unsigned i = 0; i < 2 * masterCycles; ++i)
                edge();
            via.reset = 0;
            continue;
        }
        unsigned wait, write, addr, data, value, flags, irq, pb7;
        std::istringstream row(line);
        if (!cases || !(row >> wait >> write >> addr >> data >> value >> flags >> irq >> pb7))
            return 2;
        row >> std::ws;
        if (!row.eof())
            return 2;
        unsigned actual = 0, sampledIrq = 0;
        for (unsigned cycle = 0; cycle <= wait; ++cycle) {
            for (unsigned phase = 0; phase < masterCycles; ++phase) {
                via.io_phi2 = phase >= masterCycles / 2;
                via.io_tick = phase + 1 == masterCycles;
                via.io_read = !write && phase + 1 == masterCycles && cycle == wait;
                via.io_write = write && phase + 1 == masterCycles && cycle == wait;
                via.io_address = addr;
                via.io_data = data;
                via.clk = 0;
                via.eval();
                if (phase + 1 == masterCycles && cycle == wait) {
                    actual = via.io_result;
                    sampledIrq = via.io_irq;
                }
                via.clk = 1;
                via.eval();
            }
        }
        via.io_tick = via.io_read = via.io_write = 0;
        via.io_address = 13;
        via.eval();
        unsigned actualFlags = via.io_result;
        via.io_address = 0;
        via.eval();
        unsigned actualPb7 = via.io_result >> 7;
        ++operations;
        if ((!write && actual != value) || actualFlags != flags || sampledIrq != irq ||
            actualPb7 != pb7) {
            if (differences < 24)
                std::cout << "case " << caseId << " operation " << operations << " input " << line
                          << " actual " << actual << ' ' << actualFlags << ' ' << sampledIrq << ' '
                          << actualPb7 << '\n';
            ++differences;
        }
    }
    std::cout << "VIA timer histories: " << cases << " cases; " << operations << " operations; "
              << differences << " differences\n";
    if (!cases || !operations)
        return 2;
    return differences ? 1 : 0;
}
