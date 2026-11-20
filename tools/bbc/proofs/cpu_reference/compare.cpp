// Independently execute the original C pin engine and its symbolic adapter.
#define CHIPS_IMPL
#include "VM6502ReferenceNext.h"
#include "m6502.h"
#include "reference_fields.h"
#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

using State = std::array<uint32_t, 8>;
static uint64_t seed = 0xe117ea5a9464b451ULL;
static uint64_t randomWord() {
    seed ^= seed << 13;
    seed ^= seed >> 7;
    seed ^= seed << 17;
    return seed;
}
static void put(State &state, unsigned offset, unsigned width, uint64_t value) {
    for (unsigned bit = 0; bit < width; bit++)
        state[(offset + bit) / 32] |= uint32_t((value >> bit) & 1) << ((offset + bit) % 32);
}
static State pack(const m6502_t &cpu) {
    State state{};
    unsigned offset = 256;
#define PACK_FIELD(name, width)                                                                    \
    offset -= width;                                                                               \
    put(state, offset, width, cpu.name);
    REFERENCE_FIELDS(PACK_FIELD)
#undef PACK_FIELD
    return state;
}
int main(int argc, char **argv) {
    const unsigned count = argc == 2 ? std::strtoul(argv[1], nullptr, 10) : 100000;
    if (!count)
        return 2;
    VM6502ReferenceNext model;
    for (unsigned caseId = 0; caseId < count; caseId++) {
        m6502_t cpu{};
#define RANDOM_FIELD(name, width) cpu.name = static_cast<decltype(cpu.name)>(randomWord());
        REFERENCE_FIELDS(RANDOM_FIELD)
#undef RANDOM_FIELD
        cpu.IR = ValidMicrocodes[caseId % ValidMicrocodes.size()];
        const auto before = pack(cpu);
        uint64_t pins = randomWord();
        const uint64_t incoming = pins;
        for (unsigned word = 0; word < 8; word++)
            model.state_in[word] = before[word];
        model.pins_in = pins;
        model.eval();
        pins = m6502_tick(&cpu, pins);
        const auto expected = pack(cpu);
        bool equal = !model.trap && model.pins_out == pins;
        for (unsigned word = 0; word < 8; word++)
            equal = equal && model.state_out[word] == expected[word];
        if (!equal) {
            std::cerr << "C adapter mismatch case " << caseId << " IR "
                      << ValidMicrocodes[caseId % ValidMicrocodes.size()] << " incoming "
                      << std::hex << incoming << " pins " << model.pins_out << "/" << pins
                      << " trap " << unsigned(model.trap) << "\n";
            for (unsigned word = 0; word < 8; word++)
                if (model.state_out[word] != expected[word])
                    std::cerr << "state word " << word << " " << model.state_out[word] << "/"
                              << expected[word] << " input " << before[word] << "\n";
            return 1;
        }
    }
    std::cout << count
              << " independent C tick transitions; all 256 state bits and 64 returned pins agree\n";
    return 0;
}
