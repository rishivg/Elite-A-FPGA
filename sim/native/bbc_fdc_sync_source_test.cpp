#include "VFdcSyncSource.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

struct Frame {
    uint64_t age, shift;
    unsigned count, checking;
};
using Expected = std::array<uint64_t, 8>;

int main(int argc, char **argv) {
    try {
        if (argc != 4)
            throw std::runtime_error("Expected source directory, frame count and partial count");
        const std::string root = argv[1];
        const unsigned expectedFrames = std::stoul(argv[2]), expectedPartial = std::stoul(argv[3]);
        std::ifstream inputs(root + "/frames.tsv"),
            oracle(root + "/original.bin", std::ios::binary), partial(root + "/short.tsv");
        if (!inputs || !oracle || !partial || !expectedFrames || !expectedPartial)
            throw std::runtime_error("Missing original sync fixtures");
        std::vector<Frame> frames;
        Frame frame{};
        while (inputs >> frame.age >> frame.shift >> frame.count >> frame.checking) {
            if (frame.age > (uint64_t(1) << 53) || frame.shift > UINT32_MAX || frame.count > 15 ||
                frame.checking > 1)
                throw std::runtime_error("Invalid source prefix frame");
            frames.push_back(frame);
        }
        if (!inputs.eof() || frames.size() != expectedFrames)
            throw std::runtime_error("Incomplete original sync frame inventory");
        VFdcSyncSource v;
        auto compare = [&](unsigned id, unsigned word, unsigned length, const Expected &expected) {
            const auto &f = frames.at(id);
            v.age = f.age;
            v.shift = f.shift;
            v.count = f.count;
            v.checking = f.checking;
            v.word = word;
            v.length = length;
            v.eval();
            const Expected actual{v.followingAge,      v.followingShift, v.followingCount,
                                  v.followingChecking, v.candidate,      v.clockByte,
                                  v.dataByte,          v.candidateBit};
            return !v.trap && actual == expected;
        };
        for (unsigned id = 0; id < frames.size(); ++id) {
            for (unsigned word = 0; word < 65536; ++word) {
                unsigned char bytes[18];
                if (!oracle.read(reinterpret_cast<char *>(bytes), sizeof(bytes)))
                    throw std::runtime_error("Truncated original sync oracle");
                Expected expected{};
                for (unsigned i = 0; i < 8; ++i)
                    expected[0] |= uint64_t(bytes[i]) << (8 * i);
                for (unsigned i = 0; i < 4; ++i)
                    expected[1] |= uint64_t(bytes[8 + i]) << (8 * i);
                for (unsigned i = 2; i < expected.size(); ++i)
                    expected[i] = bytes[10 + i];
                if (!compare(id, word, 16, expected)) {
                    std::cout << "SYNC_PREFIX_MISMATCH word frame " << id << " word " << word
                              << '\n';
                    return 1;
                }
            }
        }
        if (oracle.get() != std::char_traits<char>::eof())
            throw std::runtime_error("Extra original sync oracle records");
        unsigned id, word, length, checked = 0;
        while (partial >> id >> word >> length) {
            Expected expected{};
            for (auto &value : expected)
                if (!(partial >> value))
                    throw std::runtime_error("Malformed partial original sync record");
            if (id >= frames.size() || word > 65535 || length > 15)
                throw std::runtime_error("Invalid partial source prefix frame");
            if (!compare(id, word, length, expected)) {
                std::cout << "SYNC_PREFIX_MISMATCH partial frame " << id << " word " << word
                          << '\n';
                return 1;
            }
            ++checked;
        }
        if (!partial.eof() || checked != expectedPartial)
            throw std::runtime_error("Incomplete partial source prefix inventory");
        std::cout << "Original sync prefix: " << frames.size() * 65536 << " word cases; " << checked
                  << " partial cases; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
