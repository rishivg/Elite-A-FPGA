// MC6850/serial ULA against independent original scheduler observations.
#include "VBbcSerial.h"
#include "VBbcSerial___024root.h"
#include <fstream>
#include <iostream>
#include <string>

int main(int argc, char **argv) {
    if (argc != 3 && argc != 4)
        return 2;
    const bool omitIdle = argc == 4 && std::string(argv[3]) == "--idle-proven";
    if (argc == 4 && !omitIdle)
        return 2;
    std::ifstream input(argv[1]);
    unsigned multiplier = std::stoul(argv[2]);
    if (!input || (multiplier != 1 && multiplier != 3))
        return 2;
    unsigned cases = 0, observations = 0, differences = 0;
    std::string token;
    while (input >> token) {
        if (token == "total") {
            unsigned expectedCases, expectedObservations;
            std::string extra;
            if (!(input >> expectedCases >> expectedObservations) || input >> extra ||
                expectedCases != cases || expectedObservations != observations)
                return 2;
            std::cout << "BBC serial: " << cases << " cases; " << observations << " observations; "
                      << differences << " differences\n";
            return differences ? 1 : 0;
        }
        unsigned id, rows;
        if (token != "case" || !(input >> id >> rows) || id != cases || rows < 1 || rows > 20)
            return 2;
        VBbcSerial dut;
        dut.io_tick = dut.io_aciaRead = dut.io_aciaWrite = dut.io_serialRead = dut.io_serialWrite =
            0;
        dut.io_address = dut.io_data = 0;
        dut.reset = 1;
        dut.clk = 0;
        dut.eval();
        dut.clk = 1;
        dut.eval();
        dut.reset = 0;
        unsigned current = 0;
        for (unsigned row = 0; row < rows; row++) {
            unsigned cycle, action, data, status, irq, period, control, baud, rs423, empty, pending,
                remaining;
            if (!(input >> cycle >> action >> data >> status >> irq >> period >> control >> baud >>
                  rs423 >> empty >> pending >> remaining) ||
                cycle < current || cycle > 1000000 || action > 5 || data > 255 || status > 255 ||
                irq > 1 || control > 255 || baud > 7 || rs423 > 1 || empty > 1 || pending > 1 ||
                (cycle == current && (row != 0 || cycle != 0 || action != 0)))
                return 2;
            while (current < cycle) {
                const bool accepted = current + 1 == cycle;
                // A separately sealed RTL proof permits skipping idle edges.
                // Every tick/register endpoint still executes normally.
                for (unsigned master = omitIdle ? 16 * multiplier - 1 : 0; master < 16 * multiplier;
                     master++) {
                    const bool endpoint = master + 1 == 16 * multiplier;
                    dut.io_tick = endpoint;
                    dut.io_aciaRead = accepted && endpoint && action == 5;
                    dut.io_aciaWrite = accepted && endpoint && (action == 1 || action == 2);
                    dut.io_serialRead = accepted && endpoint && action == 4;
                    dut.io_serialWrite = accepted && endpoint && action == 3;
                    dut.io_address = action == 2 || action == 5;
                    dut.io_data = data;
                    dut.clk = 0;
                    dut.eval();
                    dut.clk = 1;
                    dut.eval();
                }
                current++;
            }
            // The parent tick and accepted strobes also settle low after their
            // edge; holding them high would falsely forward the next deadline.
            dut.io_tick = dut.io_aciaRead = dut.io_aciaWrite = dut.io_serialRead =
                dut.io_serialWrite = 0;
            dut.io_address = 0;
            dut.eval();
            auto &state = *dut.rootp;
            if (dut.io_result != status || dut.io_irq != irq ||
                state.BbcSerial__DOT__transmitPeriod != period ||
                state.BbcSerial__DOT__control != control || state.BbcSerial__DOT__baud != baud ||
                state.BbcSerial__DOT__rs423 != rs423 || state.BbcSerial__DOT__empty != empty ||
                state.BbcSerial__DOT__pending != pending ||
                state.BbcSerial__DOT__remaining != remaining) {
                differences++;
                if (differences < 9)
                    std::cerr << "Serial mismatch: case " << id << " cycle " << cycle << " status "
                              << unsigned(dut.io_result) << "/" << status << " IRQ "
                              << unsigned(dut.io_irq) << "/" << irq << "\n";
            }
            observations++;
        }
        cases++;
    }
    return 2;
}
