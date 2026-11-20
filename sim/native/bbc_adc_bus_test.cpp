// NEC uPD7002 bus checks, derived from datasheet pages 8.78 and 8.81–8.84.
// The conversion clock here deliberately retains the emulator's nominal periods.
#include "VAdc7002.h"
#include <array>
#include <iostream>
#include <stdexcept>

static unsigned observations = 0;
static void edge(VAdc7002 &v) {
    v.clk = 0;
    v.eval();
    v.clk = 1;
    v.eval();
}
static void check(bool condition) {
    ++observations;
    if (!condition)
        throw std::runtime_error("NEC ADC bus observation " + std::to_string(observations));
}
static unsigned peek(VAdc7002 &v, unsigned address) {
    v.io_address = address;
    v.eval();
    return v.io_result;
}
static unsigned read(VAdc7002 &v, unsigned address) {
    v.io_address = address;
    v.io_read = 1;
    v.eval();
    const unsigned result = v.io_result;
    edge(v);
    v.io_read = 0;
    v.eval();
    return result;
}
static void start(VAdc7002 &v, unsigned control, unsigned value) {
    v.io_channels = uint64_t(value) * 0x0001000100010001ULL;
    v.io_write = 1;
    v.io_address = 0;
    v.io_data = control;
    edge(v);
    v.io_write = 0;
    v.eval();
}
static void finish(VAdc7002 &v, unsigned control) {
    v.io_tick = 1;
    for (unsigned i = 0; i < ((control & 8) ? 10000 : 4000); ++i)
        edge(v);
    v.io_tick = 0;
    v.eval();
}
int main() {
    try {
        constexpr std::array<unsigned, 4> values{0x123f, 0x4567, 0x8abc, 0xfed1};
        unsigned cases = 0;
        for (unsigned previous = 0; previous < 16; ++previous)
            for (unsigned next = 0; next < 16; ++next) {
                VAdc7002 v;
                v.io_tick = v.io_read = v.io_write = v.io_address = v.io_data = 0;
                v.io_channels = 0;
                v.reset = 1;
                edge(v);
                v.reset = 0;
                check(peek(v, 0) == 0x40 && v.io_eocN == 0);
                const unsigned oldValue = values[previous & 3];
                const unsigned newValue = values[(next + 1) & 3];
                start(v, previous, oldValue);
                check(peek(v, 0) == 0x80 && v.io_eocN == 1);
                finish(v, previous);
                const unsigned oldFields = previous | ((oldValue >> 10) & 0x30);
                check(read(v, 0) == (0x40 | oldFields) && v.io_eocN == 0);
                check(read(v, 2) == (oldValue & 0xf0) && v.io_eocN == 0);
                check(read(v, 3) == (oldValue & 0xf0) && v.io_eocN == 0);
                check(read(v, 1) == (oldValue >> 8) && v.io_eocN == 1);
                check(peek(v, 0) == (0xc0 | oldFields));
                start(v, next, newValue);
                check(read(v, 0) == (0x80 | oldFields) && v.io_eocN == 1);
                check(read(v, 2) == (oldValue & 0xf0));
                check(read(v, 3) == (oldValue & 0xf0));
                check(read(v, 1) == (oldValue >> 8));
                check(peek(v, 0) == (0x80 | oldFields));
                finish(v, next);
                const unsigned newFields = next | ((newValue >> 10) & 0x30);
                check(peek(v, 0) == (0x40 | newFields) && v.io_eocN == 0);
                check(read(v, 1) == (newValue >> 8) && v.io_eocN == 1);
                check(read(v, 2) == (newValue & 0xf0));
                check(read(v, 3) == (newValue & 0xf0));
                check(peek(v, 0) == (0xc0 | newFields));
                ++cases;
            }
        std::cout << "{\"passed\":true,\"cases\":" << cases << ",\"observations\":" << observations
                  << "}\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
