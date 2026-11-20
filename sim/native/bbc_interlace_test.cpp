#ifndef VBbcVideo
#include "VBbcVideo.h"
#endif
#include <array>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <tuple>
#include <vector>
#ifdef BBC_TELETEXT_CURSOR_WARM
#include "bbc_own_prefix.h"
#include "verilated_save.h"
#endif
#ifndef BBC_MASTER_TICKS
#define BBC_MASTER_TICKS 16
#endif
#ifndef BBC_EXPECT_CASES
#define BBC_EXPECT_CASES 16
#endif
#ifndef BBC_FIRST_FIELD
#define BBC_FIRST_FIELD 6
#endif
#ifndef BBC_LAST_FIELD
#define BBC_LAST_FIELD 7
#endif
static unsigned pixelField(unsigned field, unsigned y) {
#ifdef BBC_TELETEXT_INPUT
    // Compare physical field parities, independently of emulator startup
    // field numbering. The retained capture window still spans exactly two
    // complete consecutive fields on each device; never retime input writes.
    return y & 1;
#else
    return field;
#endif
}
using Key = std::tuple<unsigned, unsigned, unsigned>;
int main(int argc, char **argv) {
    try {
        if (argc != 2
#ifdef BBC_TELETEXT_CURSOR_WARM
            && (argc != 3 || std::string(argv[2]) != "warm")
#endif
        )
            throw std::runtime_error("usage: interlace FIXTURE");
        std::ifstream f(argv[1]);
        std::string kind;
        unsigned total;
        if (!(f >> kind >> total) || kind != "cases" ||
            (total != BBC_EXPECT_CASES
#ifdef BBC_REFERENCE_CASES
             && total != BBC_REFERENCE_CASES
#endif
             ))
            throw std::runtime_error("Bad header");
        unsigned long long pixels = 0, differences = 0;
#ifdef BBC_TELETEXT_CURSOR_WARM
        const bool warm = argc == 3;
        const bool borders = total == BBC_REFERENCE_CASES;
        struct Prefix {
            std::filesystem::path state;
            unsigned nextStep, field, cellField;
            uint64_t elapsed, lastDot;
            bool hadDot;
        };
        OwnPrefixDirectory cache;
        std::map<std::string, Prefix> prefixes;
#endif
        for (unsigned caseId = 0; caseId < total; caseId++) {
            unsigned id, interlace, fast, mode, steps, operations, count;
            if (!(f >> kind >> id >> interlace >> fast >> mode >> steps >> operations >> count) ||
                kind != "case" || id != caseId)
                throw std::runtime_error("Bad case");
            if (steps == 0 || steps > 2000000 || operations > 1000 || operations > steps ||
                count == 0 || count > 1000000)
                throw std::runtime_error("Invalid case bounds");
            std::vector<std::array<unsigned, 3>> ops(operations);
            for (auto &op : ops)
                if (!(f >> op[0] >> op[1] >> op[2]) || op[0] < 1 || op[0] > 2 || op[1] > 1 ||
                    op[2] > 255)
                    throw std::runtime_error("Invalid bus operation");
#ifdef BBC_TELETEXT_CURSOR_WARM
            const unsigned pixelOrigin =
                borders && ops.size() == 33 ? 12 * (2 + ((ops[17][2] >> 4) & 3)) : 0;
#endif
            std::map<Key, std::array<unsigned, 3>> expected, actual;
            for (unsigned i = 0; i < count; i++) {
                unsigned field, x, y, r, g, b;
                if (!(f >> field >> x >> y >> r >> g >> b) || field < BBC_FIRST_FIELD ||
                    field > BBC_LAST_FIELD || x >= 2048 || y >= 1024 || r > 255 || g > 255 ||
                    b > 255)
                    throw std::runtime_error("Invalid original pixel");
                if (!expected
                         .emplace(Key{pixelField(field, y), x, y}, std::array<unsigned, 3>{r, g, b})
                         .second)
                    throw std::runtime_error("Repeated original pixel");
            }
            VBbcVideo v;
            v.io_crtcWrite = v.io_lightPenPulse = v.io_ulaWrite = v.io_address = v.io_data =
                v.io_scroll = v.io_memoryData = 0;
            auto edge = [&]() {
                v.clk = 0;
                v.eval();
                v.clk = 1;
                v.eval();
            };
            v.reset = 1;
            edge();
            edge();
            v.reset = 0;
            unsigned field = 0, cellField = 0;
#ifdef BBC_TELETEXT_INPUT
            unsigned long long elapsed = 0, lastDot = 0;
            bool hadDot = false;
#endif
            unsigned firstStep = 0;
#ifdef BBC_TELETEXT_CURSOR_WARM
            auto neutral = ops;
            std::string prefixKey;
            bool prefixReady = false;
            bool previousVsync = false;
            unsigned programStep = 0;
            if (warm) {
                if (mode > 2 || ops.size() != 33)
                    throw std::runtime_error(
                        "Warm teletext cursor requires a complete register recipe");
                for (unsigned r = 0; r < 16; ++r)
                    if (ops[2 * r] != std::array<unsigned, 3>{1, 0, r} || ops[2 * r + 1][0] != 1 ||
                        ops[2 * r + 1][1] != 1)
                        throw std::runtime_error("Unexpected cursor initialization bus recipe");
                neutral[17][2] &= 0x3f;
                neutral[21][2] = 32;
                neutral[23][2] = neutral[29][2] = neutral[31][2] = 0;
                neutral[32] = {2, 0, 2};
                prefixKey.push_back(char(mode));
                for (const auto &op : neutral)
                    for (unsigned value : op)
                        prefixKey.push_back(char(value));
                const auto saved = prefixes.find(prefixKey);
                if (saved != prefixes.end()) {
                    VerilatedRestore restore;
                    restore.open(saved->second.state.string());
                    restore >> v;
                    restore.close();
                    const auto &prefix = saved->second;
                    firstStep = programStep = prefix.nextStep;
                    field = prefix.field;
                    cellField = prefix.cellField;
                    elapsed = prefix.elapsed;
                    lastDot = prefix.lastDot;
                    hadDot = prefix.hadDot;
                    prefixReady = true;
                }
            }
#endif
            for (unsigned step = firstStep; step < steps; step++)
                for (unsigned phase = 0; phase < BBC_MASTER_TICKS; phase++) {
                    v.io_crtcWrite = v.io_ulaWrite = 0;
                    unsigned operation = step;
                    const auto *program = &ops;
#ifdef BBC_TELETEXT_CURSOR_WARM
                    if (warm) {
                        program = prefixReady ? &ops : &neutral;
                        operation = prefixReady ? step - programStep : step;
                    }
#endif
                    if (operation < program->size() && phase == BBC_MASTER_TICKS - 1) {
                        const auto &op = (*program)[operation];
                        v.io_crtcWrite = op[0] == 1;
                        v.io_ulaWrite = op[0] == 2;
                        v.io_address = op[1];
                        v.io_data = op[2];
                    }
                    v.clk = 0;
                    v.eval();
                    const bool fetch = v.io_memoryRead;
#ifdef BBC_TELETEXT_CURSOR_WARM
                    const bool vsyncRise = v.io_busVsync && !previousVsync;
                    previousVsync = v.io_busVsync;
#endif
                    const unsigned a = v.io_memoryAddress;
                    if (fetch)
                        cellField = field;
#ifdef BBC_TELETEXT_INPUT
                    if (v.io_control & 2) {
                        if (v.io_unsupported)
                            throw std::runtime_error("Teletext output marked unsupported");
                        if (v.io_pixel) {
                            if (hadDot && elapsed - lastDot != 8)
                                throw std::runtime_error("Teletext dots are not exactly 12 MHz");
                            lastDot = elapsed;
                            hadDot = true;
                        }
                    } else
                        hadDot = false;
                    elapsed++;
#endif
                    unsigned outputX = v.io_x;
                    bool collect = v.io_display;
#ifdef BBC_TELETEXT_CURSOR_WARM
                    if (borders) {
                        // The independent fixture declares CRTC columns 0..46,
                        // including both borders for either display skew. Use
                        // its fixed counter origin and retain every blanking dot.
                        outputX = (outputX + pixelOrigin) & 2047;
                        if (v.io_pixel && cellField >= BBC_FIRST_FIELD &&
                            cellField <= BBC_LAST_FIELD && v.io_display && v.io_y < 20 &&
                            outputX >= 564) {
                            std::cerr << "case " << id
                                      << " cursor outside window: raw x=" << unsigned(v.io_x)
                                      << " projected x=" << outputX << " y=" << unsigned(v.io_y)
                                      << " field=" << cellField
                                      << " control=" << unsigned(v.io_control) << "\n";
                            throw std::runtime_error("Shown cursor exceeds the original window");
                        }
                        collect = outputX < 564 && v.io_y < 20;
                    }
#endif
                    if (v.io_pixel && collect && cellField >= BBC_FIRST_FIELD &&
                        cellField <= BBC_LAST_FIELD) {
                        const Key key{pixelField(cellField, v.io_y), outputX, v.io_y};
                        const std::array<unsigned, 3> rgb{(v.io_rgb & 1) ? 255u : 0u,
                                                          (v.io_rgb & 2) ? 255u : 0u,
                                                          (v.io_rgb & 4) ? 255u : 0u};
                        if (!actual.emplace(key, rgb).second)
                            throw std::runtime_error("Repeated DUT pixel");
                    }
                    if (v.io_frame)
                        field++;
                    v.clk = 1;
                    v.eval();
                    if (fetch)
                        v.io_memoryData = ((a * 73) ^ (a >> 5) ^ 0xa6) & 255;
#ifdef BBC_TELETEXT_INPUT
                    if (fetch) {
                        constexpr unsigned text[] = {1,  69, 76, 73, 84, 69, 45,   65, 32, 2,
                                                     70, 80, 71, 65, 32, 19, 0x7f, 30, 26, 0x3f,
                                                     31, 32, 13, 65, 66, 67, 12,   8,  65, 9,
                                                     24, 65, 7,  65, 29, 32, 28,   32};
                        v.io_memoryData = mode == 2 ? 32
                                          : mode
                                              ? text[(a & 0x3ff) % (sizeof(text) / sizeof(text[0]))]
                                              : ((a * 17) ^ (a >> 3)) & 127;
                    }
#endif
#ifdef BBC_TELETEXT_CURSOR_WARM
                    if (warm && !prefixReady && field == BBC_FIRST_FIELD - 1 && vsyncRise) {
                        if (phase != BBC_MASTER_TICKS - 1)
                            throw std::runtime_error(
                                "Own cursor prefix is not on a shared bus boundary");
                        const auto path = cache.path / (std::to_string(prefixes.size()) + ".state");
                        VerilatedSave saved;
                        saved.open(path.string());
                        saved << v;
                        saved.close();
                        prefixes.emplace(prefixKey, Prefix{path, step + 1, field, cellField,
                                                           elapsed, lastDot, hadDot});
                        prefixReady = true;
                        programStep = step + 1;
                    }
#endif
                }
            if (expected.size() != actual.size()) {
                std::cerr << "case " << id << " counts " << expected.size() << "/" << actual.size()
                          << " fields " << field << "\n";
                differences++;
            }
            unsigned shown = 0;
            for (const auto &[key, rgb] : expected) {
                const auto it = actual.find(key);
                if (it == actual.end() || it->second != rgb) {
                    differences++;
                    if (shown++ < 3)
                        std::cerr << "case " << id << " key " << std::get<0>(key) << ","
                                  << std::get<1>(key) << "," << std::get<2>(key) << " expected "
                                  << rgb[0] << "," << rgb[1] << "," << rgb[2] << " actual "
                                  << (it == actual.end() ? -1 : int(it->second[0])) << ","
                                  << (it == actual.end() ? -1 : int(it->second[1])) << ","
                                  << (it == actual.end() ? -1 : int(it->second[2])) << "\n";
                }
            }
            pixels += expected.size();
        }
        if (f.peek() != std::char_traits<char>::eof()) {
            f >> std::ws;
            if (!f.eof())
                throw std::runtime_error("Trailing fixture");
        }
        std::cout << "Field RGB: " << total << " cases; " << pixels << " pixels; " << differences
                  << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
