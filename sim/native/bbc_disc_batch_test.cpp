// Process-local snapshots contain only this DUT's independently executed state.
#include "VIntel8271.h"
#include "bbc_disc_payload.h"
#include "bbc_own_prefix.h"
#include "verilated_save.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>
struct Prefix {
    std::string file;
    int64_t clock;
};
int main(int argc, char **argv) {
    try {
        if (argc != 5)
            throw std::runtime_error("Expected manifest, SSD, output directory, cache flag");
        const std::string option = argv[4];
        if (option != "0" && option != "1")
            throw std::runtime_error("Invalid cache flag");
        bool cache = option == "1";
        std::ifstream disk(argv[2], std::ios::binary), manifest(argv[1]);
        if (!disk || !manifest)
            throw std::runtime_error("Missing batch input");
        std::vector<uint8_t> initial{std::istreambuf_iterator<char>(disk), {}};
        if (initial.empty() || initial.size() > 204800)
            throw std::runtime_error("Invalid SSD");
        const auto encoded = bbc_fm::encode_ssd(initial);
        OwnPrefixDirectory scratch;
        std::unordered_map<std::string, Prefix> prefixes;
        uint64_t executed = 0, hits = 0;
        std::string fixture, name;
        while (manifest >> fixture >> name) {
            auto model = std::make_unique<VIntel8271>();
            auto &v = *model;
            static_assert(sizeof(v.io_storageData) == sizeof(uint16_t),
                          "Raw FM backing store requires generated 16-bit storage ports");
            auto image = encoded;
            std::ifstream input(fixture);
            std::ofstream writes(std::string(argv[3]) + "/" + name + ".writes.tsv");
            std::ofstream rawWrites(std::string(argv[3]) + "/" + name + ".raw.tsv");
            if (!input || !writes || !rawWrites)
                throw std::runtime_error("Missing fixture/output");
            v.io_read = 0;
            v.io_write = 0;
            v.io_warmReset = 0;
            v.io_address = 0;
            v.io_data = 0;
            v.io_storageData = 0;
            int64_t clock = -1;
            unsigned physicalWrites = 0;
            auto edge = [&]() {
                v.clk = 0;
                v.eval();
                auto d = image.at(v.io_storageAddress);
                if (v.io_storageData != d) {
                    v.io_storageData = d;
                    v.eval();
                }
                if (!v.reset && v.io_storageWrite) {
                    rawWrites << (clock + 1 + 15) / 16 << ' ' << v.io_storageAddress << ' '
                              << unsigned(v.io_storageWriteData) << '\n';
                    if (const auto address = bbc_fm::payload_address(v.io_storageAddress))
                        writes << (clock + 1 + 15) / 16 << ' ' << *address << ' '
                               << unsigned(uint8_t(v.io_storageWriteData)) << '\n';
                    image.at(v.io_storageAddress) = v.io_storageWriteData;
                    physicalWrites++;
                }
                v.clk = 1;
                v.eval();
                executed++;
            };
            v.reset = 1;
            edge();
            edge();
            v.reset = 0;
            std::string line, key;
            unsigned rows = 0, differences = 0;
            while (std::getline(input, line)) {
                std::istringstream parsed(line);
                uint64_t time;
                char op;
                unsigned address, data, nmi;
                if (!(parsed >> time >> op >> address >> data >> nmi) || time > 20000000 ||
                    address > 7 || data > 255 || nmi > 1 || (op != 'R' && op != 'W' && op != 'P'))
                    throw std::runtime_error("Malformed bus fixture");
                std::string extra;
                if (parsed >> extra)
                    throw std::runtime_error("Extra bus fixture fields");
                const int64_t target = time * 16, before = target - 1;
                if (clock > before)
                    throw std::runtime_error("Unordered bus fixture");
                const std::string waitKey = key + "@idle " + std::to_string(before);
                key += line + '\n';
                // The key includes every input and observed output of the prefix.
                // Only successful prefixes without external storage writes are reusable.
                auto saved = prefixes.find(key);
                if (cache && rows < 32 && physicalWrites == 0 && differences == 0 &&
                    saved != prefixes.end()) {
                    VerilatedRestore state;
                    state.open(saved->second.file.c_str());
                    state >> v;
                    state.close();
                    clock = saved->second.clock;
                    hits++;
                    rows++;
                    continue;
                }
                // Reuse a fully executed idle interval before accepting the next access.
                // Its key contains only past matched rows and this boundary's time;
                // upcoming address/data and observations still execute and compare.
                auto waiting = prefixes.find(waitKey);
                const bool longWait = before - clock > 1024;
                if (cache && rows < 32 && physicalWrites == 0 && differences == 0 &&
                    waiting != prefixes.end()) {
                    VerilatedRestore state;
                    state.open(waiting->second.file.c_str());
                    state >> v;
                    state.close();
                    clock = waiting->second.clock;
                    hits++;
                } else {
                    while (clock < before) {
                        edge();
                        clock++;
                    }
                    if (cache && rows < 32 && longWait && physicalWrites == 0 && differences == 0 &&
                        prefixes.size() < 128) {
                        const std::string file =
                            (scratch.path / std::to_string(prefixes.size())).string();
                        VerilatedSave state;
                        state.open(file.c_str());
                        state << v;
                        state.close();
                        prefixes.emplace(waitKey, Prefix{file, clock});
                    }
                }
                v.io_address = address;
                v.io_data = data;
                v.clk = 0;
                v.eval();
                unsigned actual = v.io_result;
                if (v.io_nmi != nmi || (op != 'W' && actual != data)) {
                    if (differences++ < 20)
                        std::cerr << name << " row " << rows << " epoch " << time << " data "
                                  << actual << '/' << data << " nmi " << unsigned(v.io_nmi) << '/'
                                  << nmi << '\n';
                }
                if (op == 'R')
                    v.io_read = 1;
                else if (op == 'W')
                    v.io_write = 1;
                edge();
                clock++;
                v.io_read = 0;
                v.io_write = 0;
                if (cache && rows < 32 && differences == 0 && physicalWrites == 0 &&
                    prefixes.size() < 128) {
                    std::string file = (scratch.path / std::to_string(prefixes.size())).string();
                    VerilatedSave state;
                    state.open(file.c_str());
                    state << v;
                    state.close();
                    prefixes.emplace(key, Prefix{file, clock});
                }
                rows++;
            }
            if (!input.eof() || rows < 100)
                throw std::runtime_error("Truncated fixture");
            std::ofstream report(std::string(argv[3]) + "/" + name + ".log");
            report << rows << " original bus observations; " << differences << " differences\n";
            if (differences)
                return 1;
        }
        if (!manifest.eof())
            throw std::runtime_error("Malformed manifest");
        std::cout << "executed " << executed << " cached_rows " << hits << " states "
                  << prefixes.size() << '\n';
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
