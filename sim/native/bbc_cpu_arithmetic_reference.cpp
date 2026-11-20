// Public pin API of the unchanged, checksum-pinned original NMOS CPU model.
#define CHIPS_IMPL
#include "bbc_cpu_arithmetic_stimulus.h"
#include "m6502-original.h"
#include <fstream>
#include <iostream>

int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    std::ofstream output(argv[1], std::ios::binary);
    output.write(reinterpret_cast<const char *>(BbcArithmeticHeader.data()),
                 BbcArithmeticHeader.size());
    std::array<uint8_t, 65536> ram{};
    unsigned cases = 0;
    for (unsigned operation = 0; operation < BbcArithmeticOperations; ++operation)
        for (unsigned flags = 0; flags < BbcArithmeticFlags; ++flags)
            for (unsigned accumulator = 0; accumulator < 256; ++accumulator)
                for (unsigned operand = 0; operand < 256; ++operand) {
                    arithmetic_program(ram, operation, flags, accumulator, operand);
                    m6502_t cpu;
                    m6502_desc_t desc{};
                    uint64_t pins = m6502_init(&cpu, &desc);
                    bool done = false;
                    for (unsigned cycle = 0; cycle < 64; ++cycle) {
                        const unsigned address = M6502_GET_ADDR(pins);
                        if ((pins & M6502_SYNC) && address == BbcArithmeticEnd) {
                            output.put(char(cpu.A));
                            output.put(char(cpu.P & 0xcf));
                            done = true;
                            ++cases;
                            break;
                        }
                        if (pins & M6502_RW) {
                            M6502_SET_DATA(pins, ram[address]);
                        } else {
                            ram[address] = M6502_GET_DATA(pins);
                        }
                        pins = m6502_tick(&cpu, pins);
                    }
                    if (!done)
                        return 1;
                }
    output.flush();
    if (!output || cases != BbcArithmeticCases)
        return 1;
    std::cout << "Original arithmetic: " << cases << " cases\n";
    return 0;
}
