// Every Model B address against unchanged original dispatch/wait-state APIs.
#include "VBbcAddressDecode.h"
#include <fstream>
#include <iostream>

int main(int argc, char **argv) {
    if (argc != 2)
        return 2;
    std::ifstream input(argv[1]);
    if (!input)
        return 2;
    VBbcAddressDecode dut;
    unsigned address, reads, writes, slow, count = 0, differences = 0;
    int fallback;
    while (input >> address >> reads >> writes >> slow >> fallback) {
        if (address != count || address > 65535 || reads > 127 || writes > 511 || slow > 1 ||
            fallback < -1 || fallback > 255)
            return 2;
        dut.io_address = address;
        dut.eval();
        const unsigned actual = dut.io_crtc | (dut.io_acia << 1) | (dut.io_serial << 2) |
                                (dut.io_systemVia << 3) | (dut.io_userVia << 4) |
                                (dut.io_fdc << 5) | (dut.io_adc << 6) | (dut.io_ula << 7) |
                                (dut.io_romSelect << 8);
        if ((actual & 127) != reads || actual != writes || dut.io_slow != slow ||
            (fallback >= 0 && dut.io_unimplemented != fallback)) {
            differences++;
            if (differences < 9)
                std::cerr << "Decode mismatch at " << std::hex << address << "\n";
        }
        count++;
    }
    std::cout << "Model B decode: " << count << " addresses; " << differences << " differences\n";
    return !input.eof() || count != 65536 ? 2 : differences ? 1 : 0;
}
