// Passive comparison of the live machine pins with independent cold video.
// Neither expected pixels nor reference CPU/RAM state are driven into the DUT.
#pragma once
#include <array>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <zlib.h>

class BbcColdVideoInput {
    gzFile file;
    std::array<unsigned char, 65536> buffer{};
    size_t cursor = 0, available = 0;

  public:
    explicit BbcColdVideoInput(const char *variable) : file(nullptr) {
        const char *path = std::getenv(variable);
        if (!path || !*path || !(file = gzopen(path, "rb")))
            throw std::runtime_error("Missing independent cold video stream");
        if (gzbuffer(file, 1 << 20)) {
            gzclose(file);
            throw std::runtime_error("Cannot buffer independent cold video stream");
        }
    }
    ~BbcColdVideoInput() { gzclose(file); }
    BbcColdVideoInput(const BbcColdVideoInput &) = delete;
    BbcColdVideoInput &operator=(const BbcColdVideoInput &) = delete;
    template <size_t N> bool read(std::array<unsigned char, N> &packet) {
        for (size_t i = 0; i < N; i++) {
            if (cursor == available) {
                const int length = gzread(file, buffer.data(), buffer.size());
                if (length < 0 || (!length && (!gzeof(file) || i)))
                    throw std::runtime_error("Truncated independent cold video stream");
                if (!length)
                    return false;
                available = length;
                cursor = 0;
            }
            packet[i] = buffer[cursor++];
        }
        return true;
    }
};

class BbcColdVideoObserver {
    BbcColdVideoInput pins{"BBC_COLD_VIDEO_PINS"}, cells{"BBC_COLD_VIDEO_CELLS"},
        bitmap{"BBC_COLD_VIDEO_BITMAP"};
    std::array<unsigned char, 20> packet{};
    std::array<unsigned char, 16> character{};
    std::array<unsigned char, 8> bitmapDots{};
    struct Cell {
        uint64_t first = 0, rgb = 0;
        unsigned x = 0, y = 0;
    } current, previous;
    uint64_t slots = 0, edges = 0, samples = 0, teletextDots = 0, bitmapCount = 0,
             syncObservations = 0, syncDifferences = 0, pixelDifferences = 0,
             controlDifferences = 0, coordinateDifferences = 0, rgbDifferences = 0,
             unsupportedEdges = 0, expectedTeletextDots = 0, expectedBitmapDots = 0;
    unsigned bitmapDot = 0;
    static uint32_t u32(const unsigned char *p) {
        return p[0] | (p[1] << 8) | (p[2] << 16) | (uint32_t(p[3]) << 24);
    }
    static uint64_t u64(const unsigned char *p) { return u32(p) | (uint64_t(u32(p + 4)) << 32); }
    void difference(uint64_t &count, const char *kind, uint64_t physical, unsigned actual,
                    unsigned expected) {
        if (count++ < 8)
            std::cerr << "Cold video " << kind << " at physical edge " << physical << " actual "
                      << actual << " expected " << expected << "\n";
    }

  public:
    BbcColdVideoObserver() {
        for (const char *name : {"BBC_LOAD_STATE", "BBC_SAVE_STATE"}) {
            const char *value = std::getenv(name);
            if (value && *value)
                throw std::runtime_error("Cold video observer forbids saved-state inputs");
        }
    }
    template <class Machine> void before(uint64_t physical, const Machine &dut) {
        if (physical < 768) {
            if (!dut.reset)
                throw std::runtime_error("Cold video reset ended early");
            return;
        }
        if (dut.reset || physical != 768 + edges)
            throw std::runtime_error("Cold video edge history is incomplete");
        const unsigned phase = physical % 48;
        const uint64_t clock = physical / 48;
        if (!phase) {
            if (!pins.read(packet) || !bitmap.read(bitmapDots) || u32(packet.data()) != clock ||
                packet[19])
                throw std::runtime_error("Invalid or incomplete original video slot");
            if (!(packet[11] & 64)) {
                if (!cells.read(character) || u32(character.data()) != clock)
                    throw std::runtime_error("Independent character clocks differ");
                previous = current;
                const unsigned xy = u32(character.data() + 4);
                // The declared serializer phase is also used by the standalone
                // chip checker: load at edge 20, first dot at edge 23.
                current = {clock * 48 + 23, u64(character.data() + 8), xy & 65535, xy >> 16};
                if (current.rgb >> 36)
                    throw std::runtime_error("Invalid independent character colors");
                samples++;
            }
            bitmapDot = 0;
        }
        if (dut.io_videoControl != packet[12])
            difference(controlDifferences, "control", physical, dut.io_videoControl, packet[12]);
        const unsigned sync = unsigned(dut.io_vsync) | (unsigned(dut.io_hsync) << 1);
        const unsigned expectedSync = (packet[11] >> 1) & 3;
        if (sync != expectedSync)
            difference(syncDifferences, "sync before", physical, sync, expectedSync);
        syncObservations++;
        const bool teletext = packet[12] & 2;
        const bool expectedPixel = teletext ? phase % 8 == 7 : phase % 6 == 5;
        if (bool(dut.io_pixel) != expectedPixel)
            difference(pixelDifferences, "pixel clock", physical, dut.io_pixel, expectedPixel);
        // A faulty DUT pixel strobe cannot remove an expected observation.
        if (expectedPixel) {
            unsigned expectedRgb;
            if (teletext) {
                const Cell &cell = physical >= current.first ? current : previous;
                if (!cell.first || physical < cell.first || (physical - cell.first) % 8)
                    throw std::runtime_error("Invalid independent dot/character association");
                const unsigned dot = (physical - cell.first) / 8;
                if (dot >= 12)
                    throw std::runtime_error("Extra independent character dot");
                const unsigned x = (cell.x + dot) & 2047;
                if (dut.io_videoX != x || dut.io_videoY != cell.y)
                    difference(coordinateDifferences, "coordinate", physical,
                               unsigned(dut.io_videoX) | (unsigned(dut.io_videoY) << 16),
                               x | (cell.y << 16));
                expectedRgb = (cell.rgb >> (3 * dot)) & 7;
                expectedTeletextDots++;
                teletextDots += bool(dut.io_pixel);
            } else {
                if (bitmapDot >= 8 || bitmapDots[bitmapDot] > 7)
                    throw std::runtime_error("Invalid independent bitmap dot");
                expectedRgb = bitmapDots[bitmapDot++];
                expectedBitmapDots++;
                bitmapCount += bool(dut.io_pixel);
            }
            if (dut.io_rgb != expectedRgb)
                difference(rgbDifferences, "RGB", physical, dut.io_rgb, expectedRgb);
        }
        unsupportedEdges += bool(dut.io_unsupportedVideo) || (teletext && (packet[12] & 16));
        edges++;
    }
    template <class Machine> void after(uint64_t physical, const Machine &dut) {
        if (physical < 768 || physical % 48 != 47)
            return;
        if (bitmapDot != ((packet[12] & 2) ? 0 : 8))
            throw std::runtime_error("Incomplete independent bitmap slot");
        const unsigned sync = unsigned(dut.io_vsync) | (unsigned(dut.io_hsync) << 1);
        if (sync != (packet[18] & 3))
            difference(syncDifferences, "sync after", physical, sync, packet[18] & 3);
        syncObservations++;
        slots++;
    }
    void finish(const std::filesystem::path &output, uint64_t cycles, bool prefix) {
        const bool boundary = cycles > 256 && cycles % 16 == 0 && edges == cycles * 3 - 768 &&
                              slots * 48 == edges && syncObservations == slots * 49 &&
                              samples == (slots + 1) / 2;
        bool originalEnd = false;
        if (!prefix)
            originalEnd = !pins.read(packet) && !bitmap.read(bitmapDots) && !cells.read(character);
        const bool passed =
            boundary && (prefix || originalEnd) && !syncDifferences && !pixelDifferences &&
            !controlDifferences && !coordinateDifferences && !rgbDifferences && !unsupportedEdges &&
            teletextDots == expectedTeletextDots && bitmapCount == expectedBitmapDots;
        std::ofstream result(output / "cold-video.json");
        result << "{\"passed\":" << (passed ? "true" : "false")
               << ",\"prefix\":" << (prefix ? "true" : "false")
               << ",\"original_end\":" << (originalEnd ? "true" : "false")
               << ",\"boundary_complete\":" << (boundary ? "true" : "false")
               << ",\"slots\":" << slots << ",\"physical_edges\":" << edges
               << ",\"character_samples\":" << samples << ",\"teletext_dots\":" << teletextDots
               << ",\"bitmap_dots\":" << bitmapCount
               << ",\"expected_teletext_dots\":" << expectedTeletextDots
               << ",\"expected_bitmap_dots\":" << expectedBitmapDots
               << ",\"sync_observations\":" << syncObservations
               << ",\"sync_differences\":" << syncDifferences
               << ",\"pixel_differences\":" << pixelDifferences
               << ",\"control_differences\":" << controlDifferences
               << ",\"coordinate_differences\":" << coordinateDifferences
               << ",\"rgb_differences\":" << rgbDifferences
               << ",\"unsupported_edges\":" << unsupportedEdges << "}\n";
        result.flush();
        if (!result)
            throw std::runtime_error("Cannot finish cold video observations");
        if (!passed)
            throw std::runtime_error("Live cold machine video differs from independent original");
    }
};
