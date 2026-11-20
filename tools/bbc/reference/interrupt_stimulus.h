// Shared declared CPU stimulus; expected bus requests come only from the pinned NMOS core.
#pragma once
#include <array>
#include <cstdint>
#include <vector>
inline std::array<uint8_t, 65536> interrupt_ram(unsigned kind, unsigned vector, unsigned &base) {
    const std::vector<std::vector<uint8_t>> tails = {{0xea},
                                                     {0xad, 0, 3},
                                                     {0x8d, 0, 3},
                                                     {0xd0, 0},
                                                     {0xf0, 0},
                                                     {0x78},
                                                     {0x58},
                                                     {0x28},
                                                     {0xd0, 2},
                                                     {0x40},
                                                     {0xde, 0, 3},
                                                     {0xf0, 0, 0xde, 0, 3},
                                                     {0xea, 0xde, 0, 3},
                                                     {0xee, 0, 3}};
    std::array<uint8_t, 65536> initial{};
    initial.fill(0xea);
    std::vector<uint8_t> code = {0xa2, 0xff, 0x9a, 0xa9, 0x77, 0x58};
    if (kind == 6)
        code.back() = 0xea;
    if (kind == 7) {
        code[4] = 0x20;
        code[5] = 0x48;
    }
    if (kind == 9) {
        code[1] = 0xfc;
        code[5] = 0xea;
        initial[0x1fd] = 0x20;
        initial[0x1fe] = 0x20;
        initial[0x1ff] = 4;
    }
    code.insert(code.end(), tails[kind].begin(), tails[kind].end());
    code.insert(code.end(), 128, 0xea);
    code.insert(code.end(), {0x4c, 0x06, 0x04});
    base = kind == 8 ? 0x4fd : 0x406;
    for (unsigned i = 0; i < code.size(); i++)
        initial[base - 6 + i] = code[i];
    initial[0xfffc] = (base - 6) & 255;
    initial[0xfffd] = (base - 6) >> 8;
    initial[vector] = 0;
    initial[vector + 1] = 0x30;
    initial[0x3000] = 0x4c;
    initial[0x3001] = 0;
    initial[0x3002] = 0x30;
    return initial;
}
