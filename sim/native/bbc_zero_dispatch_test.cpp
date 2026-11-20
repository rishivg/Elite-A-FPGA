// Compare original bus returns and CPU edge retention at the exact accepted slot.
#include "VDispatchProbe.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
int main(int argc, char **argv) {
    try {
        if (argc != 3)
            throw std::runtime_error("Expected fixture and clock multiplier");
        const unsigned multiplier = std::stoul(argv[2]);
        if (multiplier != 1 && multiplier != 3)
            throw std::runtime_error("Invalid clock multiplier");
        std::ifstream fixture(argv[1]);
        std::string marker, name;
        unsigned id, count, cases = 0, observations = 0;
        while (fixture >> marker >> id >> name >> count) {
            if (marker != "case" || count == 0)
                throw std::runtime_error("Bad fixture header");
            VDispatchProbe v;
            v.reset = 1;
            v.rd = 0;
            v.wr = 0;
            v.address = 0;
            v.data = 0;
            v.base = 0;
            auto resetEdge = [&]() {
                v.clk = 0;
                v.eval();
                v.clk = 1;
                v.eval();
            };
            resetEdge();
            resetEdge();
            v.reset = 0;
            unsigned clock = 0, edges = 0;
            for (unsigned row = 0; row < count; ++row) {
                unsigned cycle, address, data, status, nmi, expectedEdges;
                char action;
                if (!(fixture >> cycle >> action >> address >> data >> status >> nmi >>
                      expectedEdges))
                    throw std::runtime_error("Truncated fixture row");
                const unsigned target = cycle * 16 * multiplier;
                if (target <= clock || address > 7 ||
                    (action != 'W' && action != 'R' && action != 'P'))
                    throw std::runtime_error("Invalid fixture order");
                unsigned returned = 0;
                while (clock < target) {
                    ++clock;
                    v.base = clock % multiplier == 0;
                    v.rd = clock == target && action == 'R';
                    v.wr = clock == target && action == 'W';
                    v.address = address;
                    v.data = data;
                    v.clk = 0;
                    v.eval();
                    if (v.nmi && !v.previous)
                        ++edges;
                    if (clock == target)
                        returned = v.result;
                    v.clk = 1;
                    v.eval();
                }
                v.rd = 0;
                v.wr = 0;
                v.address = 0;
                v.eval();
                // Bus returns and rises were sampled before this accepted edge.
                // Observe committed latches afterward, rather than the next
                // combinational peripheral callback already becoming visible.
                if ((action != 'W' && returned != data) ||
                    (action != 'W' && v.storedStatus != status) || v.storedNmi != nmi ||
                    edges != expectedEdges) {
                    std::cout << "DISPATCH_MISMATCH " << name << " row " << row << " cycle "
                              << cycle << " return " << returned << '/' << data << " status "
                              << +v.storedStatus << '/' << status << " nmi " << +v.storedNmi << '/'
                              << nmi << " edges " << edges << '/' << expectedEdges << '\n';
                    return 1;
                }
                ++observations;
            }
            ++cases;
        }
        if (!fixture.eof() || cases != 18)
            throw std::runtime_error("Incomplete case inventory");
        std::cout << "Zero dispatch: " << cases << " cold cases; " << observations
                  << " observations; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
