// Normal reset/stack prefixes, followed by independent NMOS bus responses.
// This is a bounded discovery corpus; it is not an arbitrary-history proof.
#define CHIPS_IMPL
#include "VBbcCpu.h"
#include "m6502.h"
#include "official_stimulus.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

struct Input {
    uint8_t a, x, y, s, p, opcode;
    std::array<uint8_t, 10> read;
    uint16_t base;
};
struct Bus {
    uint16_t address;
    uint8_t data;
    bool write;
    bool operator==(const Bus &other) const {
        return address == other.address && data == other.data && write == other.write;
    }
};
struct Result {
    std::vector<Bus> bus;
    uint16_t pc;
    uint8_t a, x, y, s, p;
};
static Bus prefix(unsigned step, const Input &in) {
    const std::array<uint16_t, 26> address = {0,
                                              0,
                                              0x100,
                                              0x1ff,
                                              0x1fe,
                                              0xfffc,
                                              0xfffd,
                                              in.base,
                                              uint16_t(in.base + 1),
                                              uint16_t(in.base + 2),
                                              uint16_t(in.base + 3),
                                              uint16_t(in.base + 3),
                                              uint16_t(in.base + 4),
                                              uint16_t(in.base + 5),
                                              uint16_t(in.base + 6),
                                              uint16_t(0x100 | in.s),
                                              uint16_t(in.base + 6),
                                              uint16_t(in.base + 7),
                                              uint16_t(in.base + 8),
                                              uint16_t(in.base + 9),
                                              uint16_t(in.base + 10),
                                              uint16_t(in.base + 11),
                                              uint16_t(in.base + 12),
                                              uint16_t(in.base + 13),
                                              uint16_t(0x100 | uint8_t(in.s - 1)),
                                              uint16_t(0x100 | in.s)};
    const std::array<uint8_t, 26> data = {0xea,
                                          0xea,
                                          0xea,
                                          0xea,
                                          0xea,
                                          uint8_t(in.base),
                                          uint8_t(in.base >> 8),
                                          0xa2,
                                          in.s,
                                          0x9a,
                                          0xa9,
                                          0xa9,
                                          in.p,
                                          0x48,
                                          0xa9,
                                          in.p,
                                          0xa9,
                                          in.a,
                                          0xa2,
                                          in.x,
                                          0xa0,
                                          in.y,
                                          0x28,
                                          in.opcode,
                                          0xea,
                                          in.p};
    return {address.at(step), data.at(step), step == 15};
}
static Result original(const Input &in) {
    m6502_t cpu;
    m6502_desc_t desc{};
    uint64_t pins = m6502_init(&cpu, &desc);
    for (unsigned step = 0; step < 26; step++) {
        const auto bus = prefix(step, in);
        const Bus actual{M6502_GET_ADDR(pins), pins & M6502_RW ? bus.data : M6502_GET_DATA(pins),
                         !(pins & M6502_RW)};
        if (!(actual == bus))
            throw std::runtime_error("Original initialization bus differs");
        M6502_SET_DATA(pins, bus.data);
        pins = m6502_tick(&cpu, pins);
    }
    if (!(pins & M6502_SYNC) || M6502_GET_ADDR(pins) != uint16_t(in.base + 13) || cpu.A != in.a ||
        cpu.X != in.x || cpu.Y != in.y || cpu.S != in.s || (cpu.P & 0xcf) != (in.p & 0xcf))
        throw std::runtime_error("Original initialization results differ");
    Result result{};
    for (unsigned step = 0; step < 10; step++) {
        if (step && (pins & M6502_SYNC)) {
            result.pc = M6502_GET_ADDR(pins);
            result.a = cpu.A;
            result.x = cpu.X;
            result.y = cpu.Y;
            result.s = cpu.S;
            result.p = cpu.P & 0xcf;
            return result;
        }
        const auto data = step == 0 ? in.opcode : in.read[step];
        result.bus.push_back({M6502_GET_ADDR(pins), pins & M6502_RW ? data : M6502_GET_DATA(pins),
                              !(pins & M6502_RW)});
        M6502_SET_DATA(pins, data);
        pins = m6502_tick(&cpu, pins);
    }
    throw std::runtime_error("Original instruction did not complete");
}
static bool actual(const Input &in, const Result &expected, unsigned period) {
    VBbcCpu dut;
    dut.enable = 0;
    dut.irq = dut.nmi = dut.read_data = 0;
    auto edge = [&](uint8_t data) {
        // Exercise every physical edge, including disabled intervals. The
        // synchronous read byte reaches the core after its request edge.
        for (unsigned phase = 0; phase < period; phase++) {
            dut.enable = phase == period - 1;
            dut.clk = 0;
            dut.eval();
            dut.clk = 1;
            dut.eval();
            if (dut.enable)
                dut.read_data = data;
            dut.eval();
        }
    };
    dut.reset = 1;
    for (unsigned cycle = 0; cycle < 8; cycle++)
        edge(0xea);
    dut.reset = 0;
    auto settle = [&]() {
        dut.enable = 1;
        dut.clk = 0;
        dut.eval();
    };
    for (unsigned step = 0; step < 26; step++) {
        settle();
        const auto bus = prefix(step, in);
        const Bus observed{dut.address, dut.write_enable ? dut.write_data : bus.data,
                           bool(dut.write_enable)};
        if (!(observed == bus))
            return false;
        edge(bus.data);
    }
    for (unsigned step = 0; step < 10; step++) {
        settle();
        const bool finish = step > 1 && dut.instruction;
        const auto pc = uint16_t(dut.pc - 1);
        const uint8_t data = step == 0                    ? in.opcode
                             : step < expected.bus.size() ? in.read[step]
                                                          : 0xea;
        if (step < expected.bus.size()) {
            const Bus observed{dut.address, dut.write_enable ? dut.write_data : data,
                               bool(dut.write_enable)};
            if (!(observed == expected.bus[step]))
                return false;
        }
        // I retires before the other diagnostic flags. The final DECODE edge
        // may already execute the prefetched CLI/SEI, so observe the completed
        // instruction's I before that edge and its remaining flags afterward.
        const uint8_t interruptBefore = dut.flags & 4;
        edge(data);
        if (step == 0 && (dut.accumulator != in.a || dut.index_x != in.x || dut.index_y != in.y ||
                          dut.stack_pointer != in.s))
            return false;
        if (finish)
            return step == expected.bus.size() + 1 && pc == expected.pc &&
                   dut.accumulator == expected.a && dut.index_x == expected.x &&
                   dut.index_y == expected.y && dut.stack_pointer == expected.s &&
                   ((dut.flags & 0xcb) | interruptBefore) == expected.p;
    }
    return false;
}
static uint32_t randomState = 0x5eed1234;
static uint8_t randomByte() {
    randomState ^= randomState << 13;
    randomState ^= randomState >> 17;
    randomState ^= randomState << 5;
    return randomState;
}

static bool stackFetch(unsigned period) {
    std::array<uint8_t, 65536> referenceMemory;
    referenceMemory.fill(0xea);
    const std::array<uint8_t, 6> program{0xa9, 0x40, 0x8d, 0x00, 0x02, 0x48};
    // PHA at $01fc writes $40 (RTI) to $01fd after the dummy PC read.
    for (size_t i = 0; i < program.size(); i++)
        referenceMemory[0x01f7 + i] = program[i];
    referenceMemory[0xfffc] = 0xf7;
    referenceMemory[0xfffd] = 0x01;
    auto dutMemory = referenceMemory;
    m6502_t cpu;
    m6502_desc_t desc{};
    uint64_t pins = m6502_init(&cpu, &desc);
    VBbcCpu dut;
    dut.reset = 1;
    dut.enable = dut.irq = dut.nmi = dut.warm_reset = dut.read_data = 0;
    auto tick = [&]() {
        dut.clk = 0;
        dut.eval();
        dut.clk = 1;
        dut.eval();
    };
    for (unsigned hold = 0; hold < 8; hold++)
        tick();
    dut.reset = 0;
    for (unsigned cycle = 0; cycle < 24; cycle++) {
        dut.enable = 0;
        for (unsigned phase = 1; phase < period; phase++)
            tick();
        dut.enable = 1;
        dut.clk = 0;
        dut.eval();
        const uint16_t address = M6502_GET_ADDR(pins);
        const bool write = !(pins & M6502_RW);
        if (dut.address != address || dut.write_enable != write ||
            bool(dut.sync) != bool(pins & M6502_SYNC) ||
            (write && dut.write_data != M6502_GET_DATA(pins))) {
            std::cerr << "Stack opcode fetch differs at cycle " << cycle << '\n';
            return false;
        }
        const uint8_t held = dutMemory[dut.address];
        if (write) {
            referenceMemory[address] = M6502_GET_DATA(pins);
            dutMemory[dut.address] = dut.write_data;
        } else {
            M6502_SET_DATA(pins, referenceMemory[address]);
        }
        pins = m6502_tick(&cpu, pins);
        dut.clk = 1;
        dut.eval();
        dut.read_data = held;
    }
    return true;
}

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3)
        return 2;
    const auto period = std::stoul(argv[1]);
    if (period != 1 && period != 16 && period != 48)
        return 2;
    if (argc == 3)
        return std::string(argv[2]) == "stack-fetch" ? (stackFetch(period) ? 0 : 1) : 2;
    unsigned count = 0, differences = 0;
    for (const auto description : OfficialOpcodes)
        for (unsigned example = 0; example < 1000; example++) {
            Input in{};
            in.a = randomByte();
            in.x = randomByte();
            in.y = randomByte();
            in.s = randomByte();
            in.p = randomByte();
            in.opcode = description[0];
            in.base = uint16_t(uint16_t(randomByte()) << 8) | randomByte();
            for (auto &byte : in.read)
                byte = randomByte();
            if (!actual(in, original(in), period)) {
                differences++;
                if (differences < 9)
                    std::cerr << "Sequence " << count << " opcode " << unsigned(in.opcode)
                              << " base " << in.base << " flags " << unsigned(in.p) << "\n";
            }
            count++;
        }
    std::cout << "CPU sequences: " << count << " cases; " << differences << " differences\n";
    return differences ? 1 : 0;
}
