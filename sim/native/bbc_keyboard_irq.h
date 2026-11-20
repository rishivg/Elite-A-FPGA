#pragma once
#include "VBbcSystemProbe.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
inline int keyboard_irq(const char *path) {
    std::ifstream f(path);
    if (!f)
        return 2;
    std::string kind;
    unsigned differences = 0, observations = 0, cases = 0;
    while (f >> kind) {
        unsigned id;
        if (kind != "case" || !(f >> id) || id != cases++)
            return 2;
        VBbcSystemProbe v;
        v.tick = 0;
        v.write = 0;
        v.address = 0;
        v.data = 0;
        for (int i = 0; i < 4; i++)
            v.keys[i] = 0;
        v.keys[0] = 2;
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
        edge();
        unsigned current = 0;
        for (unsigned n = 0; n < 14; n++) {
            unsigned clock, a, d, irq, flags;
            if (!(f >> clock >> a >> d >> irq >> flags))
                return 2;
            while (current + 1 < clock) {
                ++current;
                v.tick = current % 32 == 16;
                edge();
            }
            ++current;
            v.tick = 1;
            v.address = a;
            v.data = d;
            v.write = 1;
            v.clk = 0;
            v.eval();
            unsigned accepted = v.irq;
            v.clk = 1;
            v.eval();
            v.write = 0;
            v.eval();
            if (accepted != irq) {
                ++differences;
                std::cerr << "case " << id << " clock " << clock << " IRQ " << accepted << "/"
                          << irq << " IFR " << flags << "\n";
            }
            ++observations;
        }
    }
    std::cout << cases << " cases, " << observations << " accepted bus observations, "
              << differences << " differences\n";
    if (cases != 8 || observations != 112 || !f.eof())
        return 2;
    return differences ? 1 : 0;
}

inline int keyboard_irq_edges(const char *path) {
    std::ifstream input(path);
    std::string kind;
    unsigned cases = 0, differences = 0;
    while (input >> kind) {
        unsigned id, column, row, initial, event, expected;
        if (kind != "case" || !(input >> id >> column >> row >> initial >> event >> expected) ||
            id != cases || column >= 10 || row < 1 || row > 7 || initial > 1 || event < 512 ||
            event >= 544 || expected != 1)
            return 2;
        std::array<std::array<unsigned, 3>, 8> commands{};
        for (auto &command : commands)
            if (!(input >> command[0] >> command[1] >> command[2]) || command[1] > 15 ||
                command[2] > 255)
                return 2;
        VBbcSystemProbe v;
        v.tick = v.write = v.address = v.data = 0;
        for (unsigned i = 0; i < 4; i++)
            v.keys[i] = 0;
        const unsigned bit = column * 8 + row;
        if (initial)
            v.keys[bit / 32] |= uint32_t(1) << (bit % 32);
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
        unsigned next = 0;
        for (unsigned clock = 1; clock <= event; clock++) {
            v.tick = clock % 32 == 16;
            v.write = next < commands.size() && clock == commands[next][0];
            if (v.write) {
                v.address = commands[next][1];
                v.data = commands[next++][2];
            }
            if (clock == event) {
                // Observe the CPU-facing IRQ before this edge, after applying
                // the real key input. Post-edge flags alone miss this defect.
                v.clk = 0;
                v.eval();
                if (v.irq)
                    return 2;
                v.keys[bit / 32] ^= uint32_t(1) << (bit % 32);
                v.eval();
                if (unsigned(v.irq) != expected) {
                    if (differences++ < 8)
                        std::cerr << "case " << cases << " clock " << clock << " IRQ "
                                  << unsigned(v.irq) << "/" << expected << "\n";
                }
            }
            edge();
        }
        if (next != commands.size())
            return 2;
        cases++;
    }
    std::cout << "Keyboard edge IRQ: " << cases << " cases; " << differences << " differences\n";
    return !input.eof() || cases != 8960 ? 2 : differences ? 1 : 0;
}
