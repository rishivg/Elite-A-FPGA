#include "VFdcMarkerSourceProbe.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected source table directory");
        const std::string root = argv[1];
        std::ifstream in(root + "/original.bin", std::ios::binary);
        if (!in)
            throw std::runtime_error("Missing marker table");
        VFdcMarkerSourceProbe v;
        v.value = 0;
        v.oldResult = 0;
        v.oldReady = 0;
        v.status = 0;
        std::array<unsigned char, 9> row;
        unsigned count = 0;
        while (in.read(reinterpret_cast<char *>(row.data()), row.size())) {
            v.commandByte = row[0];
            v.clockByte = row[1];
            v.dataByte = row[2];
            v.eval();
            unsigned flags = v.starting | (v.resultWritten << 1) | (v.stateWritten << 2) |
                             (v.finished << 3) | (v.crcWritten << 4);
            if (flags != row[3] || v.resultValue != row[4] || v.nextState != row[5] ||
                v.finishResult != row[6] ||
                v.markerCrc != (unsigned(row[7]) | (unsigned(row[8]) << 8))) {
                std::cout << "MARKER_SOURCE_MISMATCH record " << count << '\n';
                return 1;
            }
            ++count;
        }
        if (!in.eof() || in.gcount() || count != 16777216)
            throw std::runtime_error("Incomplete marker table");
        std::ifstream f(root + "/result.bin", std::ios::binary);
        std::vector<uint8_t> values{std::istreambuf_iterator<char>(f), {}};
        if (!f || values.size() != 256)
            throw std::runtime_error("Incomplete result-write table");
        for (unsigned value = 0; value < 256; value++) {
            v.value = value;
            v.eval();
            if (v.followingResult != values[value]) {
                std::cout << "RESULT_SOURCE_MISMATCH value " << value << '\n';
                return 1;
            }
        }
        std::ifstream irq(root + "/irq.bin", std::ios::binary);
        std::vector<uint8_t> irqValues{std::istreambuf_iterator<char>(irq), {}};
        if (!irq || irqValues.size() != 256)
            throw std::runtime_error("Incomplete IRQ-start table");
        for (unsigned status = 0; status < 256; status++) {
            v.status = status;
            v.eval();
            if (v.irqValue != irqValues[status]) {
                std::cout << "IRQ_START_SOURCE_MISMATCH status " << status << '\n';
                return 1;
            }
        }
        std::ifstream external(root + "/external.bin", std::ios::binary);
        std::vector<uint8_t> externalValues{std::istreambuf_iterator<char>(external), {}};
        if (!external || externalValues.size() != 512)
            throw std::runtime_error("Incomplete external-status table");
        for (unsigned status = 0; status < 256; status++)
            for (unsigned ready = 0; ready < 2; ready++) {
                v.status = status;
                v.oldReady = ready;
                v.eval();
                unsigned index = 2 * status + ready;
                if (v.externalValue != externalValues[index]) {
                    std::cout << "EXTERNAL_STATUS_SOURCE_MISMATCH record " << index << '\n';
                    return 1;
                }
            }
        std::ifstream setters(root + "/setter.bin", std::ios::binary);
        std::array<unsigned char, 5> setter;
        unsigned setCount = 0;
        if (!setters)
            throw std::runtime_error("Missing internal result-set table");
        while (setters.read(reinterpret_cast<char *>(setter.data()), setter.size())) {
            if (setter[2] > 1 || setter[4] > 1)
                throw std::runtime_error("Nonboolean result-ready entry");
            v.oldResult = setter[0];
            v.value = setter[1];
            v.oldReady = setter[2];
            v.eval();
            if (v.setResult != setter[3] || v.setReady != setter[4]) {
                std::cout << "SET_RESULT_SOURCE_MISMATCH record " << setCount << '\n';
                return 1;
            }
            ++setCount;
        }
        if (!setters.eof() || setters.gcount() || setCount != 131072)
            throw std::runtime_error("Incomplete internal result-set table");
        std::cout << "Original marker: 16777216 decisions; 256 result writes; 131072 internal "
                     "result sets; 256 IRQ starts; 512 external snapshots; 0 differences\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
