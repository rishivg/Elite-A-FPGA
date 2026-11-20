// Independent normal instruction streams, including IRQ/NMI entry and return.
// Both CPUs execute their own reset, input program, stack and memory writes.
#define CHIPS_IMPL
#include "VBbcCpu.h"
#include "m6502.h"
#include "official_stimulus.h"
#include <array>
#include <iostream>
#include <vector>
static uint32_t rng;
static uint8_t byte() {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return rng;
}
// Balanced calls preserve their callers' stack even when IRQ/NMI nests inside
// them. Indirect stores target scratch RAM; indirect JMP crosses a page end.
static void stackProgram(std::array<uint8_t, 65536> &ram, unsigned entry) {
    auto emit = [&](unsigned address, std::initializer_list<uint8_t> code) {
        for (auto value : code)
            ram[address++] = value;
    };
    ram[0x24] = 0;
    ram[0x25] = 0x20;
    ram[0x30ff] = uint8_t(entry);
    ram[0x3000] = uint8_t(entry >> 8);
    emit(entry, {0xa9,
                 byte(),
                 0xa2,
                 0,
                 0xa0,
                 byte(),
                 0x20,
                 0,
                 0x50,
                 0x81,
                 0x24,
                 0x91,
                 0x24,
                 0x20,
                 0,
                 0x52,
                 0x00,
                 byte(),
                 uint8_t(byte() & 1 ? 0xf8 : 0xd8),
                 0x69,
                 byte(),
                 0xe9,
                 byte(),
                 0xd0,
                 2,
                 0xea,
                 0xea,
                 0x6c,
                 0xff,
                 0x30});
    emit(0x5000,
         {0x08, 0x48, 0x8a, 0x48, 0x98, 0x48, 0xa9, byte(), 0xf8, 0x69, byte(), 0xd8, 0xe9, byte(),
          0x8d, 0,    0x21, 0x20, 0,    0x51, 0x68, 0xa8,   0x68, 0xaa, 0x68,   0x28, 0x60});
    emit(0x5100,
         {0x08, 0x48, 0xa9, byte(), 0xf8, 0x69, byte(), 0xd8, 0xe9, byte(), 0x68, 0x28, 0x60});
    for (unsigned depth = 0; depth < 8; depth++) {
        unsigned address = 0x5200 + depth * 64;
        emit(address, {0x08, 0x48, 0xa9, byte(), uint8_t(byte() & 1 ? 0xf8 : 0xd8), 0x69, byte(),
                       uint8_t(byte() & 1 ? 0x18 : 0x38)});
        if (depth < 7) {
            unsigned child = address + 64;
            emit(address + 8, {0x20, uint8_t(child), uint8_t(child >> 8)});
        } else {
            emit(address + 8, {0xe6, 0x20, 0xea});
        }
        emit(address + 11, {0x68, 0x28, 0x60});
    }
}
int main(int argc, char **argv) {
    if (argc != 2 && !(argc == 3 && std::string(argv[2]) == "stack"))
        return 2;
    const bool stack = argc == 3;
    unsigned period = std::stoul(argv[1]);
    if (period != 1 && period != 16 && period != 48)
        return 2;
    for (unsigned scenario = 0; scenario < 64; scenario++) {
        rng = 0x5eed1234 + scenario * 0x10001;
        std::array<uint8_t, 65536> ram;
        ram.fill(0xea);
        for (unsigned i = 0; i < 0x4000; i++)
            ram[i] = byte();
        std::vector<std::array<unsigned, 2>> ops;
        for (auto op : OfficialOpcodes)
            if (op[1] < Jump || op[1] == Relative) {
                if (op[0] != 0x81 && op[0] != 0x91)
                    ops.push_back(op);
            }
        unsigned pc = 0x4000;
        const uint8_t setup[] = {0xa2,   byte(), 0x9a,   0xa9, byte(), 0x48, 0xa9,
                                 byte(), 0xa2,   byte(), 0xa0, byte(), 0x28};
        for (auto value : setup)
            ram[pc++] = value;
        const unsigned entry = pc;
        while (pc < 0x7000) {
            auto op = ops[byte() % ops.size()];
            ram[pc++] = op[0];
            switch (op[1]) {
            case Implied:
                break;
            case Relative:
                ram[pc++] = 0;
                break;
            case Immediate:
            case ZeroPage:
            case ZeroPageX:
            case ZeroPageY:
            case IndexedIndirect:
            case IndirectIndexed:
                ram[pc++] = byte();
                break;
            default:
                ram[pc++] = byte();
                ram[pc++] = 0x20 + (byte() & 15);
                break;
            }
        }
        ram[pc++] = 0x4c;
        ram[pc++] = uint8_t(entry);
        ram[pc++] = uint8_t(entry >> 8);
        if (stack)
            stackProgram(ram, entry);
        const uint8_t handler[] = {0x48, 0x8a, 0x48, 0x98, 0x48, 0xe6, 0xfe,
                                   0x68, 0xa8, 0x68, 0xaa, 0x68, 0x40};
        for (unsigned i = 0; i < sizeof(handler); i++)
            ram[0x8000 + i] = handler[i];
        ram[0xfffc] = 0;
        ram[0xfffd] = 0x40;
        ram[0xfffa] = ram[0xfffe] = 0;
        ram[0xfffb] = ram[0xffff] = 0x80;
        auto dutRam = ram;
        m6502_t original;
        m6502_desc_t desc{};
        uint64_t pins = m6502_init(&original, &desc);
        VBbcCpu dut;
        dut.reset = 1;
        dut.enable = 1;
        dut.irq = dut.nmi = dut.read_data = 0;
        for (unsigned i = 0; i < 8 * period; i++) {
            dut.clk = 0;
            dut.eval();
            dut.clk = 1;
            dut.eval();
        }
        dut.reset = 0;
        struct Architecture {
            unsigned cycle, pc, a, x, y, s, p;
        };
        Architecture latest{};
        bool entered = false;
        for (unsigned cycle = 0; cycle < 100000; cycle++) {
            bool irq = scenario % 4 >= 2 && cycle > 100 && cycle % 991 < 3;
            bool nmi = scenario % 4 % 2 && cycle > 100 && cycle % 787 < 2;
            dut.irq = irq;
            dut.nmi = nmi;
            dut.enable = 1;
            dut.clk = 0;
            dut.eval();
            pins =
                (pins & ~(M6502_IRQ | M6502_NMI)) | (irq ? M6502_IRQ : 0) | (nmi ? M6502_NMI : 0);
            if (pins & M6502_SYNC)
                latest = {
                    cycle,      M6502_GET_ADDR(pins),       original.A, original.X, original.Y,
                    original.S, unsigned(original.P & 0xcf)};
            const bool retire = dut.instruction;
            const unsigned retirePc = uint16_t(dut.pc - 1);
            const unsigned oldI = dut.flags & 4;
            if (retire && retirePc == entry)
                entered = true;
            const unsigned a = M6502_GET_ADDR(pins);
            bool w = !(pins & M6502_RW);
            const unsigned d = w ? M6502_GET_DATA(pins) : ram[a];
            if (dut.address != a || bool(dut.write_enable) != w || (w && dut.write_data != d)) {
                std::cerr << "FAIL scenario " << scenario << " period " << period << " cycle "
                          << cycle << " original " << a << ":" << w << ":" << d << " actual "
                          << dut.address << ":" << unsigned(dut.write_enable) << ":"
                          << unsigned(dut.write_data) << " PC " << std::hex << original.PC << "/"
                          << dut.pc << "\n";
                return 1;
            }
            if (w) {
                ram[a] = d;
                dutRam[a] = dut.write_data;
            } else {
                M6502_SET_DATA(pins, ram[a]);
            }
            pins = m6502_tick(&original, pins);
            const uint8_t read = dutRam[dut.address];
            for (unsigned phase = 0; phase < period; phase++) {
                dut.enable = phase == period - 1;
                dut.clk = 0;
                dut.eval();
                dut.clk = 1;
                dut.eval();
                if (dut.enable)
                    dut.read_data = read;
                dut.eval();
            }
            if (retire && entered &&
                (latest.cycle + 1 != cycle || latest.pc != retirePc ||
                 dut.accumulator != latest.a || dut.index_x != latest.x ||
                 dut.index_y != latest.y || dut.stack_pointer != latest.s ||
                 ((dut.flags & 0xcb) | oldI) != latest.p)) {
                std::cerr << "ARCH FAIL scenario " << scenario << " cycle " << cycle << " SYNC "
                          << latest.cycle << " PC " << latest.pc << "/" << retirePc << " flags "
                          << latest.p << "/" << ((dut.flags & 0xcb) | oldI) << " regs " << latest.a
                          << ":" << latest.x << ":" << latest.y << ":" << latest.s << "/"
                          << unsigned(dut.accumulator) << ":" << unsigned(dut.index_x) << ":"
                          << unsigned(dut.index_y) << ":" << unsigned(dut.stack_pointer) << "\n";
                return 1;
            }
        }
        if (ram != dutRam) {
            std::cerr << "Different final RAM\n";
            return 1;
        }
        std::cout << "stream " << scenario << " period " << period << ": PASS\n";
    }
    std::cout << "CPU streams: 64 cases; 6400000 enabled cycles; period " << period
              << "; 0 differences\n";
    return 0;
}
