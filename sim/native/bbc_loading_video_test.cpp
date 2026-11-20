// Cold video component with its own RAM and accepted CPU bus inputs.
// Expected fetch addresses, sync and cursor come from the original Hitachi
// machine; expected teletext colors come from the unchanged C SAA5050.
#include "VBbcVideo96.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <zlib.h>

static uint32_t u32(const unsigned char *p) {
    return p[0] | (p[1] << 8) | (p[2] << 16) | (uint32_t(p[3]) << 24);
}
static uint64_t u64(const unsigned char *p) { return u32(p) | (uint64_t(u32(p + 4)) << 32); }
class CompressedInput {
    gzFile file;

  public:
    explicit CompressedInput(const char *path) : file(gzopen(path, "rb")) {
        if (!file || gzbuffer(file, 1 << 20))
            throw std::runtime_error("Missing gzip capture");
    }
    ~CompressedInput() {
        if (file)
            gzclose(file);
    }
    template <size_t N> bool read(std::array<unsigned char, N> &packet) {
        const int length = gzread(file, packet.data(), N);
        if (length == int(N))
            return true;
        if (length != 0 || !gzeof(file))
            throw std::runtime_error("Truncated gzip capture");
        return false;
    }
};
struct Bus {
    uint64_t clock;
    std::string kind;
    unsigned address, data;
};
struct Cell {
    uint64_t first;
    unsigned x, y;
    uint64_t rgb;
};

int main(int argc, char **argv) {
    try {
        if (argc != 5 && argc != 6)
            return 2;
        CompressedInput pins(argv[1]), cells(argv[2]);
        std::unique_ptr<CompressedInput> bitmap;
        if (argc == 6)
            bitmap = std::make_unique<CompressedInput>(argv[5]);
        std::ifstream busFile(argv[3]);
        if (!busFile)
            throw std::runtime_error("Missing original bus inputs");
        std::vector<Bus> bus;
        Bus operation;
        while (busFile >> operation.clock >> operation.kind >> operation.address >>
               operation.data) {
            if (operation.kind != "ram" && operation.kind != "crtc" && operation.kind != "ula" &&
                operation.kind != "scroll")
                throw std::runtime_error("Unknown bus operation");
            if (!bus.empty() && operation.clock < bus.back().clock)
                throw std::runtime_error("Unsorted bus input");
            bus.push_back(operation);
        }
        if (!busFile.eof())
            throw std::runtime_error("Incomplete bus input");
        // Ordinary BBC power-on contents, never a reference RAM snapshot.
        std::array<uint8_t, 32768> memory{};
        memory.fill(255);
        std::fill_n(memory.begin(), 256, 0);
        VBbcVideo96 video;
        video.io_crtcWrite = video.io_ulaWrite = video.io_lightPenPulse = video.io_address =
            video.io_data = video.io_scroll = video.io_memoryData = 0;
        video.reset = 1;
        for (unsigned i = 0; i < 768; i++) {
            video.clk = 0;
            video.eval();
            video.clk = 1;
            video.eval();
        }
        video.reset = 0;
        uint64_t clock = 16, samples = 0, dots = 0, differences = 0, coordinateDifferences = 0,
                 syncDifferences = 0, fetchDifferences = 0, fetches = 0, ramWrites = 0,
                 unsupported = 0;
        uint64_t bitmapDots = 0, bitmapDifferences = 0;
        size_t next = 0;
        Cell current{}, previous{};
        std::array<unsigned char, 20> packet{};
        std::array<unsigned char, 16> expected{};
        std::array<unsigned char, 8> bitmapExpected{};
        while (pins.read(packet)) {
            if (u32(packet.data()) != clock || packet[19])
                throw std::runtime_error("Noncontiguous or invalid original pin stream");
            if (bitmap && !bitmap->read(bitmapExpected))
                throw std::runtime_error("Incomplete original bitmap dots");
            unsigned bitmapDot = 0;
            if (!(packet[11] & 64)) {
                if (!cells.read(expected) || u32(expected.data()) != clock)
                    throw std::runtime_error("Cell/pin clocks differ");
                previous = current;
                const unsigned xy = u32(expected.data() + 4);
                // The declared three-stage 96-MHz serializer contract: input
                // at slot start, load at its seventh 32-MHz edge, next 12-MHz
                // dot at physical edge 23. This is not a chip phase measurement.
                current = {clock * 48 + 23, xy & 65535, xy >> 16, u64(expected.data() + 8)};
                samples++;
            }
            for (unsigned phase = 0; phase < 48; phase++) {
                const uint64_t physical = clock * 48 + phase;
                video.io_crtcWrite = video.io_ulaWrite = 0;
                const Bus *ramWrite = nullptr;
                while (next < bus.size() && bus[next].clock * 48 <= physical + 1) {
                    const auto &op = bus[next++];
                    if (op.clock == 16 && op.address < 0xfe00 && op.kind != "ram")
                        continue;
                    if (op.clock * 48 != physical + 1)
                        throw std::runtime_error("Missed accepted video bus input");
                    if (op.kind == "ram") {
                        if (ramWrite || op.address >= memory.size() || op.data > 255)
                            throw std::runtime_error("Invalid accepted RAM write");
                        ramWrite = &op;
                    } else if (op.kind == "scroll")
                        video.io_scroll = op.address;
                    else {
                        video.io_crtcWrite = op.kind == "crtc";
                        video.io_ulaWrite = op.kind == "ula";
                        video.io_address = op.address & 1;
                        video.io_data = op.data;
                    }
                }
                video.clk = 0;
                video.eval();
                const bool fetch = video.io_memoryRead;
                uint8_t fetched = 0;
                if (fetch) {
                    if (ramWrite)
                        throw std::runtime_error("CPU/video RAM phases collided");
                    const unsigned address = packet[16] | (packet[17] << 8);
                    fetched = memory[video.io_memoryAddress];
                    if (video.io_memoryAddress != address || fetched != packet[15]) {
                        if (fetchDifferences++ < 8)
                            std::cerr << "Fetch at " << physical << " address "
                                      << video.io_memoryAddress << "/" << address << " data "
                                      << unsigned(fetched) << "/" << unsigned(packet[15]) << "\n";
                    }
                    fetches++;
                }
                if (unsigned(video.io_vsync) != ((packet[11] >> 1) & 1) ||
                    unsigned(video.io_hsync) != ((packet[11] >> 2) & 1)) {
                    if (syncDifferences++ < 8)
                        std::cerr << "Sync before edge " << physical << "\n";
                }
                if ((video.io_control & 2) && (video.io_control & 16))
                    unsupported++;
                if (bitmap && video.io_pixel && !(video.io_control & 2)) {
                    if (bitmapDot >= 8 || bitmapExpected[bitmapDot] > 7)
                        throw std::runtime_error("Invalid original bitmap dot");
                    if (video.io_rgb != bitmapExpected[bitmapDot]) {
                        if (bitmapDifferences++ < 16)
                            std::cerr << "Bitmap RGB at " << physical << " dot " << bitmapDot
                                      << " actual " << unsigned(video.io_rgb) << " expected "
                                      << unsigned(bitmapExpected[bitmapDot]) << "\n";
                    }
                    bitmapDot++;
                    bitmapDots++;
                }
                if (video.io_pixel && (video.io_control & 2) && !(video.io_control & 16)) {
                    const Cell &cell = physical >= current.first ? current : previous;
                    if (!cell.first || physical < cell.first || (physical - cell.first) % 8)
                        throw std::runtime_error("Invalid dot/character association");
                    const unsigned dot = (physical - cell.first) / 8;
                    if (dot >= 12)
                        throw std::runtime_error("Extra teletext dot");
                    const unsigned color = (cell.rgb >> (3 * dot)) & 7, x = (cell.x + dot) & 2047;
                    if (video.io_x != x || video.io_y != cell.y) {
                        if (coordinateDifferences++ < 8)
                            std::cerr << "Coordinate at " << physical << " " << video.io_x << ","
                                      << video.io_y << " expected " << x << "," << cell.y << "\n";
                    }
                    if (video.io_rgb != color) {
                        if (differences++ < 16)
                            std::cerr << "RGB at " << physical << " x " << x << " y " << cell.y
                                      << " actual " << unsigned(video.io_rgb) << " expected "
                                      << color << "\n";
                    }
                    dots++;
                }
                video.clk = 1;
                video.eval();
                if (fetch)
                    video.io_memoryData = fetched;
                if (ramWrite) {
                    memory[ramWrite->address] = ramWrite->data;
                    ramWrites++;
                }
                if (phase == 47 && (unsigned(video.io_vsync) != (packet[18] & 1) ||
                                    unsigned(video.io_hsync) != ((packet[18] >> 1) & 1))) {
                    if (syncDifferences++ < 8)
                        std::cerr << "Sync after slot " << clock << "\n";
                }
            }
            if (bitmap && bitmapDot != ((packet[12] & 2) ? 0 : 8))
                throw std::runtime_error("Incorrect bitmap dot count");
            clock++;
        }
        if (cells.read(expected) || next != bus.size() || (bitmap && bitmap->read(bitmapExpected)))
            throw std::runtime_error("Incomplete original capture");
        std::ofstream ram(argv[4], std::ios::binary);
        ram.write(reinterpret_cast<const char *>(memory.data()), memory.size());
        if (!ram)
            throw std::runtime_error("Cannot save own video RAM");
        std::cout << "{\"slots\":" << clock - 16 << ",\"character_samples\":" << samples
                  << ",\"teletext_dots\":" << dots
                  << ",\"coordinate_differences\":" << coordinateDifferences
                  << ",\"rgb_differences\":" << differences
                  << ",\"sync_differences\":" << syncDifferences
                  << ",\"fetch_differences\":" << fetchDifferences << ",\"fetches\":" << fetches
                  << ",\"ram_writes\":" << ramWrites << ",\"unsupported_edges\":" << unsupported
                  << ",\"sync_observations\":" << (clock - 16) * 49
                  << ",\"bitmap_dots\":" << bitmapDots
                  << ",\"bitmap_differences\":" << bitmapDifferences << "}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
