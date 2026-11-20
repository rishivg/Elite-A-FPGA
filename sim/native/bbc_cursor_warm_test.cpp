#ifndef VBbcVideo
#include "VBbcVideo.h"
#endif
#ifndef BBC_MASTER_MULTIPLIER
#define BBC_MASTER_MULTIPLIER 1
#endif
#include "bbc_own_prefix.h"
#include "verilated_save.h"
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <unistd.h>
static uint32_t word(const unsigned char *p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("usage: cursor probe FIXTURE");
        std::ifstream input(argv[1], std::ios::binary);
        std::array<unsigned char, 36> bytes{};
        if (!input.read(reinterpret_cast<char *>(bytes.data()), 12) ||
            std::string(reinterpret_cast<char *>(bytes.data()), 8) != "BCURSOR2" ||
            word(bytes.data() + 8) != 298)
            throw std::runtime_error("invalid cursor corpus");
        uint64_t differences = 0, observations = 0;
        std::map<std::string, std::filesystem::path> baselines;
        OwnPrefixDirectory cache;
        for (unsigned id = 0; id < 298; id++) {
            if (!input.read(reinterpret_cast<char *>(bytes.data()), bytes.size()) ||
                word(bytes.data()) != id)
                throw std::runtime_error("invalid cursor case");
            std::array<unsigned, 8> p{};
            for (unsigned i = 0; i < p.size(); i++)
                p[i] = word(bytes.data() + 4 + i * 4);
            if (p[0] > 1 || p[1] > 7 || p[2] > 3 || p[3] > 31 || p[4] > 31 || p[5] > 3 ||
                p[6] > 991 || p[7] < 6 || p[7] > 33)
                throw std::runtime_error("invalid cursor parameters");
            const unsigned regs[] = {p[0] ? 127u : 63u,
                                     p[0] ? 64u : 32u,
                                     p[0] ? 90u : 45u,
                                     p[0] ? 40u : 36u,
                                     38,
                                     0,
                                     31,
                                     34,
                                     0,
                                     7,
                                     32,
                                     0,
                                     12,
                                     0,
                                     0,
                                     0};
            VBbcVideo video;
            video.io_crtcWrite = video.io_lightPenPulse = video.io_ulaWrite = video.io_address =
                video.io_data = video.io_scroll = video.io_memoryData = 0;
            auto edge = [&]() {
                for (unsigned subcycle = 0; subcycle < BBC_MASTER_MULTIPLIER; subcycle++) {
                    video.clk = 0;
                    video.eval();
                    video.clk = 1;
                    video.eval();
                }
            };
            video.reset = 1;
            edge();
            edge();
            video.reset = 0;
            for (unsigned r = 0; r < 16; r++) {
                video.io_crtcWrite = 1;
                video.io_address = 0;
                video.io_data = r;
                edge();
                video.io_address = 1;
                video.io_data = regs[r];
                edge();
                video.io_crtcWrite = 0;
            }
            auto ula = [&](unsigned a, unsigned d) {
                video.io_ulaWrite = 1;
                video.io_address = a;
                video.io_data = d;
                edge();
                video.io_ulaWrite = 0;
            };
            ula(0, (p[0] ? 16 : 0) | 8);
            for (unsigned i = 0; i < 16; i++)
                ula(1, (i << 4) | (i & 7));
            std::array<unsigned char, 1024 * 512> frame{};
            unsigned fields = 0;
            bool previous = video.io_busVsync;
            auto clock = [&]() {
                video.clk = 0;
                video.eval();
                const bool fetch = video.io_memoryRead;
                const unsigned value = ((video.io_memoryAddress >> 3) ^ 0xa5) & 255;
                if (video.io_pixel && video.io_x < 1024 && video.io_y < 512)
                    frame[video.io_y * 1024 + video.io_x] = video.io_rgb;
                const bool rising = video.io_busVsync && !previous;
                previous = video.io_busVsync;
                video.clk = 1;
                video.eval();
                if (fetch)
                    video.io_memoryData = value;
                if (rising)
                    fields++;
            };
            const unsigned warm = p[7] - 1;
            const auto key = std::to_string(p[0]) + "-" + std::to_string(warm);
            if (!baselines.count(key)) {
                for (uint64_t cycle = 0; fields < warm && cycle < uint64_t(warm + 2) * 39936 * 16 *
                                                                      BBC_MASTER_MULTIPLIER;
                     cycle++)
                    clock();
                if (fields != warm)
                    throw std::runtime_error("incomplete own cursor baseline");
                const auto path = cache.path / (key + ".state");
                VerilatedSave saved;
                saved.open(path.string());
                saved << video;
                saved.close();
                baselines[key] = path;
            } else {
                VerilatedRestore saved;
                saved.open(baselines.at(key).string());
                saved >> video;
                saved.close();
                video.eval();
                previous = video.io_busVsync;
                fields = warm;
            }
            // Actual public bus writes occur during vertical blanking. The
            // next character row and field latch the newly programmed cursor.
            auto crtc = [&](unsigned reg, unsigned data) {
                video.io_crtcWrite = 1;
                video.io_address = 0;
                video.io_data = reg;
                edge();
                video.io_address = 1;
                video.io_data = data;
                edge();
                video.io_crtcWrite = 0;
            };
            crtc(8, p[2] << 6);
            crtc(10, p[3] | (p[5] << 5));
            crtc(11, p[4]);
            const unsigned position = (0xc00 + p[6]) & 0x3fff;
            crtc(14, position >> 8);
            crtc(15, position & 255);
            ula(0, (p[0] ? 16 : 0) | 8 | (p[1] << 5));
            for (uint64_t cycle = 0;
                 fields < p[7] && cycle < 2 * 39936 * 16 * BBC_MASTER_MULTIPLIER; cycle++)
                clock();
            if (fields != p[7])
                throw std::runtime_error("incomplete cursor suffix");
            std::array<unsigned char, 512 * 248> expected{};
            if (!input.read(reinterpret_cast<char *>(expected.data()), expected.size()))
                throw std::runtime_error("truncated cursor pixels");
            uint64_t caseDifferences = 0;
            unsigned first = 0;
            for (unsigned y = 0; y < 248; y++)
                for (unsigned x = 0; x < 512; x++) {
                    if (expected[y * 512 + x] > 7)
                        throw std::runtime_error("invalid cursor RGB");
                    if (frame[y * 1024 + x] != expected[y * 512 + x]) {
                        if (!caseDifferences)
                            first = y * 512 + x;
                        caseDifferences++;
                    }
                    observations++;
                }
            if (caseDifferences)
                std::cerr << "case " << id << " fast " << p[0] << " width " << p[1] << " skew "
                          << p[2] << " raster " << p[3] << ".." << p[4] << " blink " << p[5]
                          << " field " << p[7] << ": " << caseDifferences << " pixels first "
                          << first / 512 << "," << first % 512 << "\n";
            differences += caseDifferences;
        }
        if (input.peek() != std::ifstream::traits_type::eof())
            throw std::runtime_error("trailing cursor corpus");
        std::cout << "Bitmap cursor warm: " << observations << " pixels in 298 cases; "
                  << differences << " differences\n";
        return differences ? 1 : 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
