#include "VBbcAdcValues.h"
#include "verilated_save.h"
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
extern "C" {
#include "adc.h"
extern int oracle_cb1;
}
// Save only this component's own state after genuine writes and preceding
// conversion ticks. Repeated values change only its external channel pins.
class OwnSave : public VerilatedSerialize {
  public:
    std::vector<uint8_t> bytes;
    void flush() override {
        bytes.insert(bytes.end(), m_bufp, m_cp);
        m_cp = m_bufp;
    }
};
class OwnRestore : public VerilatedDeserialize {
    size_t savedSize;
    void fill() override { throw std::runtime_error("Unexpected snapshot refill"); }

  public:
    explicit OwnRestore(const std::vector<uint8_t> &bytes) : savedSize(bytes.size()) {
        if (savedSize >= bufferInsertSize())
            throw std::runtime_error("Unexpected large ADC state");
        std::memcpy(m_bufp, bytes.data(), savedSize);
        m_cp = m_bufp;
        m_endp = m_bufp + bufferSize();
    }
    void rewind() { m_cp = m_bufp; }
    // Exact consumed length rejects any truncated or excess model state.
    void complete() {
        if (size_t(m_cp - m_bufp) != savedSize)
            throw std::runtime_error("Partial or excess restore");
    }
};
static void edge(VBbcAdcValues &v) {
    v.clk = 0;
    v.eval();
    v.clk = 1;
    v.eval();
}
static void begin(VBbcAdcValues &v, unsigned control, uint16_t value = 0) {
    v.tick = 0;
    v.write = 0;
    v.address = 0;
    v.data = 0;
    v.channels = uint64_t(value) << (16 * (control & 3));
    v.reset = 1;
    edge(v);
    edge(v);
    v.reset = 0;
    v.write = 1;
    v.data = control;
    edge(v);
    v.write = 0;
    v.tick = 1;
}
static uint32_t result(VBbcAdcValues &v) {
    v.tick = 0;
    v.address = 0;
    v.eval();
    uint32_t r = v.result;
    v.address = 1;
    v.eval();
    r |= uint32_t(v.result) << 8;
    v.address = 2;
    v.eval();
    r |= uint32_t(v.result) << 16;
    return r | (uint32_t(v.eoc) << 24);
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("usage: ADC value probe CORPUS");
        std::ofstream corpus(argv[1], std::ios::binary);
        corpus.exceptions(std::ios::badbit | std::ios::failbit);
        const auto start = std::chrono::steady_clock::now();
        unsigned cases = 0, differences = 0;
        uint64_t digest = 14695981039346656037ULL;
        auto *oracle = adc_create(1, nullptr, nullptr);
        adc_power_on_reset(oracle);
        for (unsigned control = 0; control < 16; control++) {
            const unsigned period = (control & 8) ? 10000 : 4000;
            VBbcAdcValues v;
            begin(v, control);
            for (unsigned i = 1; i < period; i++)
                edge(v);
            v.tick = 0;
            v.address = 0;
            v.eval();
            if (v.result != 0x80 || v.eoc != 1)
                throw std::runtime_error("Prefix did not reach busy retained-data state");
            OwnSave save;
            save << v;
            save.flush();
            OwnRestore restore(save.bytes);
            for (unsigned value = 0; value < 65536; value++) {
                restore.rewind();
                restore >> v;
                restore.complete();
                v.channels = uint64_t(value) << (16 * (control & 3));
                v.tick = 1;
                edge(v);
                const uint32_t actual = result(v);
                adc_set_channel_value(oracle, control & 3, value);
                adc_write(oracle, 0, control);
                adc_apply_wall_time_delta(oracle, period);
                const uint32_t expected =
                    uint32_t(adc_read(oracle, 0)) | (uint32_t(adc_read(oracle, 1)) << 8) |
                    (uint32_t(adc_read(oracle, 2)) << 16) | (uint32_t(oracle_cb1) << 24);
                char bytes[4];
                for (unsigned i = 0; i < 4; i++)
                    bytes[i] = char(expected >> (8 * i));
                corpus.write(bytes, 4);
                if (actual != expected) {
                    if (differences++ < 12)
                        std::cerr << "control " << control << " value " << value << " actual "
                                  << actual << " expected " << expected << "\n";
                }
                // Check ordinary complete execution at every quadrant's endpoints.
                if ((value & 0x3fff) == 0 || (value & 0x3fff) == 0x3fff) {
                    VBbcAdcValues full;
                    begin(full, control, value);
                    for (unsigned i = 0; i < period; i++)
                        edge(full);
                    if (result(full) != actual)
                        throw std::runtime_error(
                            "Own-prefix reuse differs from uninterrupted conversion");
                }
                digest = (digest ^ actual) * 1099511628211ULL;
                cases++;
            }
        }
        corpus.close();
        adc_destroy(oracle);
        if (cases != 1048576)
            throw std::runtime_error("Incomplete value domain");
        std::cout << "{\"cases\":" << cases << ",\"differences\":" << differences
                  << ",\"digest\":" << digest << ",\"wall_seconds\":"
                  << std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count()
                  << ",\"coverage\":\"All 65536 values, four channels, both durations and "
                     "input-flag settings; completed result/status/CB1 only; 128 uninterrupted "
                     "boundary conversions\"}\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 2;
    }
}
