// Actual production video RTL; warm states are generated and reused only by this DUT.
#ifndef VBbcVideo
#include "VBbcVideo.h"
#endif
#include "verilated_save.h"
#include <array>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <unistd.h>
#include <vector>
#ifndef BBC_MASTER_TICKS
#define BBC_MASTER_TICKS 16
#endif
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3 ||
        (argc == 3 && std::string(argv[2]) != "mode" && std::string(argv[2]) != "clock"))
        return 2;
    const bool modeTransition = argc == 3 && std::string(argv[2]) == "mode";
    const bool clockTransition = argc == 3 && std::string(argv[2]) == "clock";
    unsigned regs[] = {63, 32, 45, 36, 38, 0, 31, 34, 0, 7, 32, 8, 12, 0, 13, 130, 0, 0};
    unsigned differences = 0, cases = 0;
    uint64_t observations = 0;
    std::map<std::string, unsigned> periods;
    std::ifstream timing(std::string(argv[1]) + "/periods.tsv");
    std::string name;
    unsigned period;
    while (timing >> name >> period) {
        if (!period || period >= 80000 || (!clockTransition && period != 39936) ||
            !periods.emplace(name, period).second)
            return 2;
    }
    if (!timing.eof() || periods.size() != 200)
        return 2;
    std::map<std::string, std::filesystem::path> baselines;
    const auto cache =
        std::filesystem::temp_directory_path() / ("fpgaleet-video-" + std::to_string(getpid()));
    std::filesystem::create_directories(cache);
    for (unsigned byte : {0u, 85u, 170u, 255u, 256u})
        for (unsigned fast : {0u, 1u})
            for (unsigned mode : {0u, 1u, 2u, 3u})
                for (int event : {-1, 29697, 29698, 29699, 29700}) {
                    const auto id = std::to_string(byte) + "-" + std::to_string(fast) + "-" +
                                    std::to_string(mode) + "-" + std::to_string(event);
                    regs[0] = fast ? 127 : 63;
                    regs[1] = fast ? 64 : 32;
                    regs[2] = fast ? 90 : 45;
                    regs[3] = fast ? 40 : 36;
                    VBbcVideo v;
                    v.io_crtcWrite = 0;
                    v.io_lightPenPulse = 0;
                    v.io_ulaWrite = 0;
                    v.io_address = 0;
                    v.io_data = 0;
                    v.io_scroll = 0;
                    v.io_memoryData = byte;
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
                    // Model each public write at a 2 MHz bus endpoint. Untimed
                    // initialization loses the free-running 1 MHz phase when
                    // a later control write switches the character clock.
                    auto busEdge = [&]() {
                        const bool crtcWrite = v.io_crtcWrite, ulaWrite = v.io_ulaWrite;
                        v.io_crtcWrite = v.io_ulaWrite = 0;
                        for (unsigned phase = 0; phase < BBC_MASTER_TICKS - 1; phase++)
                            edge();
                        v.io_crtcWrite = crtcWrite;
                        v.io_ulaWrite = ulaWrite;
                        edge();
                    };
                    v.io_ulaWrite = 1;
                    v.io_address = 0;
                    v.io_data = 0;
                    busEdge();
                    v.io_ulaWrite = 0;
                    for (unsigned a = 0; a < 18; a++) {
                        v.io_crtcWrite = 1;
                        v.io_address = 0;
                        v.io_data = a;
                        busEdge();
                        v.io_address = 1;
                        v.io_data = regs[a];
                        busEdge();
                        v.io_crtcWrite = 0;
                    }
                    auto ula = [&](unsigned a, unsigned d) {
                        v.io_ulaWrite = 1;
                        v.io_address = a;
                        v.io_data = d;
                        busEdge();
                        v.io_ulaWrite = 0;
                    };
                    ula(0, (fast ? 16 : 0) | (mode << 2));
                    for (unsigned i = 0; i < 16; i++)
                        ula(1, i << 4);
                    std::array<unsigned char, 1024 * 512 * 3> frame{};
                    bool previous = v.io_busVsync;
                    unsigned fields = 0;
                    long relative = -1;
                    const auto key = std::to_string(fast) + "-" + std::to_string(mode);
                    if (baselines.count(key)) {
                        VerilatedRestore restore;
                        restore.open(baselines.at(key).string());
                        restore >> v;
                        restore.close();
                        v.io_memoryData = byte;
                        v.eval();
                        previous = v.io_busVsync;
                        fields = 5;
                        relative = 0;
                    }
                    std::vector<unsigned char> actual;
                    std::vector<unsigned char> actualPins;
                    std::vector<unsigned char> actualSync;
                    for (unsigned cycle = 0; cycle < 5000000 * (BBC_MASTER_TICKS / 16); cycle++) {
                        v.clk = 0;
                        v.eval();
                        if (relative >= 0 && relative == long(event) * BBC_MASTER_TICKS) {
                            v.io_ulaWrite = 1;
                            v.io_address = modeTransition || clockTransition ? 0 : 1;
                            v.io_data = clockTransition  ? (fast ? 0 : 16) | (mode << 2)
                                        : modeTransition ? (fast ? 16 : 0) | (((mode + 1) & 3) << 2)
                                                         : (byte ? 240 : 0) | 7;
                            v.eval();
                        }
                        const bool fetch = v.io_memoryRead;
                        const unsigned fetched = (v.io_memoryAddress >> 3) & 255;
                        if (clockTransition && relative > 0 && v.io_pixel)
                            actualPins.push_back(v.io_rgb);
                        if (v.io_pixel && v.io_display && v.io_x < 1024 && v.io_y < 512) {
                            const auto o = (v.io_y * 1024 + v.io_x) * 3;
                            for (unsigned i = 0; i < 3; i++)
                                frame[o + i] = (v.io_rgb & (1 << i)) ? 255 : 0;
                        }
                        const bool rising = v.io_busVsync && !previous;
                        previous = v.io_busVsync;
                        if (rising) {
                            if (++fields == 5) {
                                relative = 0;
                                const auto path = cache / (key + ".state");
                                VerilatedSave save;
                                save.open(path.string());
                                save << v;
                                save.close();
                                baselines[key] = path;
                            } else if (fields == 6) {
                                for (unsigned y = 0; y < 248; y++)
                                    for (unsigned x = 0; x < 256; x++)
                                        for (unsigned i = 0; i < 3; i++)
                                            actual.push_back(frame[(y * 1024 + x * 2) * 3 + i]);
                                if (relative != long(periods.at(id)) * BBC_MASTER_TICKS) {
                                    std::cerr << id << " period " << relative << "/"
                                              << long(periods.at(id)) * BBC_MASTER_TICKS
                                              << " master edges\n";
                                    return 2;
                                }
                                if (clockTransition) {
                                    v.clk = 1;
                                    v.eval();
                                    actualSync.push_back(v.io_hsync | (v.io_vsync << 1));
                                }
                                break;
                            }
                        }
                        v.clk = 1;
                        v.eval();
                        if (clockTransition && relative > 0 && relative % BBC_MASTER_TICKS == 0)
                            actualSync.push_back(v.io_hsync | (v.io_vsync << 1));
                        if (byte == 256 && fetch)
                            v.io_memoryData = fetched;
                        v.io_ulaWrite = 0;
                        if (relative >= 0)
                            relative++;
                    }
                    if (clockTransition)
                        actual.swap(actualPins);
                    if (clockTransition) {
                        std::ifstream timingPins(std::string(argv[1]) + "/" + id + ".sync",
                                                 std::ios::binary);
                        std::vector<unsigned char> expectedSync{
                            std::istreambuf_iterator<char>(timingPins), {}};
                        if (expectedSync.size() != actualSync.size() || actualSync.empty())
                            return 2;
                        for (unsigned slot = 0; slot < expectedSync.size(); slot++)
                            if (expectedSync[slot] != actualSync[slot]) {
                                if (differences++ < 12)
                                    std::cerr << id << " sync slot " << slot << " "
                                              << unsigned(actualSync[slot]) << "/"
                                              << unsigned(expectedSync[slot]) << "\n";
                            }
                    }
                    std::ifstream f(std::string(argv[1]) + "/" + id +
                                        (clockTransition ? ".pins" : ".rgb"),
                                    std::ios::binary);
                    std::vector<unsigned char> expected{std::istreambuf_iterator<char>(f), {}};
                    if (expected.size() != actual.size() || actual.empty())
                        return 2;
                    observations += actual.size() / (clockTransition ? 1 : 3);
                    unsigned pixels = 0, first = 0;
                    for (unsigned o = 0; o < expected.size(); o += clockTransition ? 1 : 3)
                        if (expected[o] != actual[o] ||
                            (!clockTransition && (expected[o + 1] != actual[o + 1] ||
                                                  expected[o + 2] != actual[o + 2]))) {
                            if (!pixels)
                                first = o / (clockTransition ? 1 : 3);
                            pixels++;
                        }
                    if (pixels) {
                        std::cerr << id << " differences " << pixels << " first ";
                        if (clockTransition)
                            std::cerr << "dot " << first << "\n";
                        else
                            std::cerr << first / 256 << "," << first % 256 << "\n";
                        const auto diagnostics =
                            std::filesystem::path(argv[1]).parent_path() / "differences";
                        std::filesystem::create_directories(diagnostics);
                        std::ofstream failed(diagnostics /
                                                 (id + (clockTransition ? ".pins" : ".rgb")),
                                             std::ios::binary);
                        failed.write(reinterpret_cast<const char *>(actual.data()), actual.size());
                    }
                    differences += pixels;
                    cases++;
                }
    std::filesystem::remove_all(cache);
    if (cases != 200)
        return 2;
    std::cout << "Video: " << cases << " cases; " << observations
              << (clockTransition ? " digital dots; " : " pixels; ") << differences
              << " differences; eight own warm-state caches\n";
    return differences ? 1 : 0;
}
