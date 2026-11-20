#include "VFdcCrcSourceProbe.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

uint64_t little(const unsigned char *p, unsigned n) {
    uint64_t value = 0;
    for (unsigned i = 0; i < n; ++i)
        value |= uint64_t(p[i]) << (8 * i);
    return value;
}

int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected CRC source table directory");
        const std::string root = argv[1];
        std::ifstream input(root + "/original.bin", std::ios::binary);
        if (!input)
            throw std::runtime_error("Missing CRC source table");
        VFdcCrcSourceProbe v;
        v.pointer = 9;
        v.status = 0;
        std::array<unsigned char, 96> row;
        unsigned cases = 0;
        while (input.read(reinterpret_cast<char *>(row.data()), row.size())) {
            const auto *p = row.data();
            if (little(p + 32, 8) > 0x20000000000000ULL)
                throw std::runtime_error("Invalid dense Number count");
            for (unsigned i = 0; i < 8; ++i)
                v.registers[i] = little(p + 4 * i, 4);
            v.stateCount = little(p + 32, 8);
            v.onDiscCrc = little(p + 40, 4);
            v.crc = little(p + 44, 2);
            v.byteValue = p[46];
            v.callContext = p[47];
            v.eval();
            bool same = true;
            for (unsigned i = 0; i < 8; ++i)
                same &= v.followingRegisters[i] == little(p + 48 + 4 * i, 4);
            const unsigned flags = v.finished | (v.completed << 1) | (v.seeking << 2) |
                                   (v.stateWritten << 3) | (unsigned(v.effectCount) << 4);
            same &= v.followingCount == little(p + 80, 8) && v.followingCrc == little(p + 88, 4) &&
                    flags == p[92] && v.finishResult == p[93] && v.seekContext == p[94] &&
                    v.nextState == p[95];
            if (!same) {
                std::cout << "ID_CRC_SOURCE_MISMATCH record " << cases << '\n';
                return 1;
            }
            ++cases;
        }
        if (!input.eof() || input.gcount() || cases != 1189888)
            throw std::runtime_error("Incomplete CRC source table");
        std::ifstream header(root + "/header.bin", std::ios::binary);
        if (!header)
            throw std::runtime_error("Missing ID stop table");
        std::ifstream statusFile(root + "/status.bin", std::ios::binary);
        std::vector<uint8_t> statusValues{std::istreambuf_iterator<char>(statusFile), {}};
        if (!statusFile || statusValues.size() != 512)
            throw std::runtime_error("Incomplete status register table");
        std::array<unsigned char, 5> record;
        unsigned stops = 0;
        while (header.read(reinterpret_cast<char *>(record.data()), record.size())) {
            if (record[0] < 9 || record[0] > 12 || record[4] > 1)
                throw std::runtime_error("Invalid default ID stop record");
            v.pointer = record[0];
            v.status = record[1];
            v.eval();
            if (v.statusValue != statusValues[2 * v.status] ||
                v.commandStatus != statusValues[2 * v.status + 1]) {
                std::cout << "ID_STATUS_SOURCE_MISMATCH status " << unsigned(v.status) << '\n';
                return 1;
            }
            if (v.followingPointer != record[2] || v.followingStatus != record[3] ||
                v.stopping != record[4]) {
                std::cout << "ID_STOP_SOURCE_MISMATCH record " << stops << '\n';
                return 1;
            }
            ++stops;
        }
        if (!header.eof() || header.gcount() || stops != 1024)
            throw std::runtime_error("Incomplete ID stop table");
        std::cout << "Original ID CRC: 1189888 body, 1024 default stop, 256 status cases; 0 "
                     "differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
