// Replay the original 8271's physical host accesses; observe actual storage writes.
#include "VIntel8271.h"
#include "bbc_disc_payload.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
int main(int argc, char **argv) {
    try {
        if (argc != 4)
            throw std::runtime_error("Expected bus fixture, SSD and payload output");
        std::ifstream disk(argv[2], std::ios::binary), input(argv[1]);
        std::ofstream writes(argv[3]);
        if (!disk || !input || !writes)
            throw std::runtime_error("Missing disc probe input/output");
        std::vector<uint8_t> payload{std::istreambuf_iterator<char>(disk), {}};
        auto image = bbc_fm::encode_ssd(payload);
        VIntel8271 v;
        static_assert(sizeof(v.io_storageData) == sizeof(uint16_t),
                      "FM backing store requires generated 16-bit storage ports");
        v.io_read = 0;
        v.io_write = 0;
        v.io_warmReset = 0;
        v.io_address = 0;
        v.io_data = 0;
        v.io_storageData = 0;
        bool writeAllowed = false;
        bool resetCellAllowed = false;
        int64_t clock = -1;
        auto edge = [&]() {
            v.clk = 0;
            v.eval();
            const auto storageData = image.at(v.io_storageAddress);
            if (v.io_storageData != storageData) {
                v.io_storageData = storageData;
                v.eval();
            }
            if (!v.reset && v.io_storageWrite) {
                if (!writeAllowed) {
                    if (!resetCellAllowed || !v.io_warmReset || v.io_storageWriteData != 65535)
                        throw std::runtime_error("Aborted payload resumed after restart");
                    resetCellAllowed = false;
                }
                // The independent source exposes writes at 2 MHz scheduler boundaries.
                // Observe the native master edge in its containing 2 MHz slot.
                if (const auto address = bbc_fm::payload_address(v.io_storageAddress))
                    writes << (clock + 1 + 15) / 16 << ' ' << *address << ' '
                           << unsigned(uint8_t(v.io_storageWriteData)) << '\n';
                image.at(v.io_storageAddress) = v.io_storageWriteData;
            }
            v.clk = 1;
            v.eval();
        };
        v.reset = 1;
        edge();
        edge();
        v.reset = 0;
        uint64_t time;
        char operation;
        unsigned address, data, nmi, rows = 0, differences = 0, restarts = 0;
        while (input >> time >> operation >> address >> data >> nmi) {
            if (time > 20000000 || address > 7 || data > 255 || nmi > 1 ||
                (operation != 'R' && operation != 'W' && operation != 'B'))
                throw std::runtime_error("Malformed physical bus fixture");
            // Both reads and writes commit on the declared master edge.
            const int64_t target = time * 16, before = target - 1;
            if (clock > before)
                throw std::runtime_error("Unordered physical bus fixture");
            while (clock < before) {
                edge();
                clock++;
            }
            v.io_address = address;
            v.io_data = data;
            if (operation == 'B') {
                if (address != 0 || (data & ~0x10u) != 0 || nmi != 0)
                    throw std::runtime_error("Malformed restart request");
                v.io_warmReset = 1;
                resetCellAllowed = true;
                writeAllowed = false;
                restarts++;
            } else if (operation == 'W' && address == 0 && (data & 0x3f) == 0x0b) {
                writeAllowed = true;
            }
            v.clk = 0;
            v.eval();
            const unsigned actual = v.io_result;
            if (v.io_nmi != nmi || ((operation == 'R' || operation == 'B') && actual != data)) {
                if (differences++ < 20)
                    std::cerr << "row " << rows << " epoch " << time << ' ' << operation << ' '
                              << address << " data " << actual << '/' << data << " nmi "
                              << unsigned(v.io_nmi) << '/' << nmi << '\n';
            }
            if (operation == 'R')
                v.io_read = 1;
            else if (operation == 'W')
                v.io_write = 1;
            edge();
            clock++;
            v.io_read = 0;
            v.io_write = 0;
            v.io_warmReset = 0;
            resetCellAllowed = false;
            rows++;
        }
        if (!input.eof() || rows < 20 || restarts != 1)
            throw std::runtime_error("Truncated bus fixture");
        std::cout << rows << " original restart observations; " << differences << " differences; "
                  << restarts << " restarts\n";
        return differences ? 1 : 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
