#include "VFdcByteSourceProbe.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
std::vector<uint8_t> read(const std::string &path, size_t bytes) {
    std::ifstream input(path, std::ios::binary);
    std::vector<uint8_t> data{std::istreambuf_iterator<char>(input), {}};
    if (!input || data.size() != bytes)
        throw std::runtime_error("Incomplete original byte-control table");
    return data;
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("Expected byte-control table directory");
        const std::string root = argv[1];
        const auto irq = read(root + "/irq.bin", 256), loss = read(root + "/loss.bin", 65536 * 2),
                   counter = read(root + "/counter.bin", 16777216 * 4);
        VFdcByteSourceProbe v;
        v.low = v.high = v.command = 0;
        for (unsigned status = 0; status < 256; status++) {
            v.status = status;
            v.eval();
            if (irq[status] > 1)
                throw std::runtime_error("Invalid IRQ oracle record");
            if (v.irqEnabled != irq[status]) {
                std::cout << "BYTE_IRQ_MISMATCH status " << status << '\n';
                return 1;
            }
            for (unsigned command = 0; command < 256; command++) {
                v.command = command;
                v.eval();
                const unsigned p = (status * 256 + command) * 2;
                const unsigned expected = loss[p] | (unsigned(loss[p + 1]) << 8);
                const unsigned actual = v.serviceOk | (v.aborted << 1) | (v.finished << 2) |
                                        (unsigned(v.finishResult) << 8);
                if (actual != expected) {
                    std::cout << "BYTE_LOSS_MISMATCH status " << status << " command " << command
                              << '\n';
                    return 1;
                }
            }
            v.command = 0;
            for (unsigned high = 0; high < 256; high++)
                for (unsigned low = 0; low < 256; low++) {
                    v.low = low;
                    v.high = high;
                    v.eval();
                    const size_t p = ((status * 256 + high) * 256 + low) * 4;
                    const uint32_t expected = counter[p] | (uint32_t(counter[p + 1]) << 8) |
                                              (uint32_t(counter[p + 2]) << 16) |
                                              (uint32_t(counter[p + 3]) << 24);
                    if (counter[p + 3] > 1)
                        throw std::runtime_error("Invalid counter oracle record");
                    const uint32_t actual = v.nextLow | (uint32_t(v.nextHigh) << 8) |
                                            (uint32_t(v.nextStatus) << 16) |
                                            (uint32_t(v.done) << 24);
                    if (actual != expected) {
                        std::cout << "BYTE_COUNTER_MISMATCH status " << status << " high " << high
                                  << " low " << low << '\n';
                        return 1;
                    }
                }
        }
        std::cout << "Original byte control: 256 IRQ, 65536 eligibility, 16777216 counter cases; 0 "
                     "differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
