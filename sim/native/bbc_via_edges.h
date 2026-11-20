#pragma once
#include "VVia6522.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

inline int via_edges(const char *path) {
    std::ifstream fixture(path);
    if (!fixture)
        throw std::runtime_error("Missing control-edge fixture");
    unsigned cases = 0, observations = 0, differences = 0;
    std::string kind;
    while (fixture >> kind) {
        unsigned id;
        if (kind != "case" || !(fixture >> id) || id != cases)
            throw std::runtime_error("Invalid control-edge case");
        VVia6522 via;
        via.io_tick = 0;
        via.io_phi2 = 0;
        via.io_read = 0;
        via.io_write = 0;
        via.io_address = 0;
        via.io_data = 0;
        via.io_portAIn = 255;
        via.io_portBIn = 255;
        via.io_ca1 = 0;
        via.io_ca2 = 0;
        via.io_ca2AfterWrite = 0;
        via.io_cb1Pulse = 0;
        via.io_cb1 = 0;
        via.io_cb2 = 0;
        auto edge = [&]() {
            via.clk = 0;
            via.eval();
            via.clk = 1;
            via.eval();
        };
        via.reset = 1;
        edge();
        edge();
        via.reset = 0;
        edge();
        unsigned count = 0;
        while (fixture >> kind) {
            if (kind == "case") {
                fixture.seekg(-std::streamoff(kind.size()), std::ios::cur);
                break;
            }
            unsigned a, b;
            if (kind == "pins") {
                if (!(fixture >> a >> b) || a > 255 || b > 255)
                    throw std::runtime_error("Invalid external pins");
                via.io_portAIn = a;
                via.io_portBIn = b;
                edge();
                continue;
            }
            unsigned levels, expected, flags, irq;
            if ((kind != "read" && kind != "write" && kind != "edge") ||
                !(fixture >> a >> b >> levels >> expected >> flags >> irq) || a > 15 || b > 255 ||
                levels > 16 || expected > 255 || flags > 255 || irq > 1)
                throw std::runtime_error("Invalid control-edge observation");
            for (unsigned phase = 0; phase < 31; phase++) {
                via.io_phi2 = phase >= 16;
                edge();
            }
            if (levels < 16) {
                via.io_ca1 = levels & 1;
                via.io_ca2 = (levels >> 1) & 1;
                via.io_cb1 = (levels >> 2) & 1;
                via.io_cb2 = (levels >> 3) & 1;
            }
            via.io_address = a;
            via.io_data = b;
            via.io_read = kind == "read";
            via.io_write = kind == "write";
            via.io_tick = 1;
            via.io_phi2 = 1;
            via.clk = 0;
            via.eval();
            const unsigned result = kind == "read" ? via.io_result : 0;
            edge();
            via.io_tick = 0;
            via.io_read = 0;
            via.io_write = 0;
            via.io_address = 13;
            via.eval();
            if (result != expected || via.io_result != flags || via.io_irq != irq) {
                if (differences++ < 12)
                    std::cerr << "case " << id << " observation " << count << ": result " << result
                              << "/" << expected << ", IFR " << unsigned(via.io_result) << "/"
                              << flags << ", IRQ " << unsigned(via.io_irq) << "/" << irq << "\n";
            }
            observations++;
            count++;
        }
        if (count != 23)
            throw std::runtime_error("Incomplete control-edge observations");
        cases++;
    }
    if (cases != 128 || !fixture.eof())
        throw std::runtime_error("Incomplete control-edge fixture");
    std::cout << "VIA edges: " << observations << " observations in " << cases << " cases; "
              << differences << " differences\n";
    return differences ? 1 : 0;
}
