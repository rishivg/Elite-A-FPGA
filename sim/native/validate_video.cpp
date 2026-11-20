// Check the complete digital RGB path against each actual presented SoC frame.
#include "VEliteTop.h"
#include "VEliteTop___024root.h"
#include "verilated.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
int main(int argc, char **argv) {
    try {
        if (argc != 2)
            throw std::runtime_error("usage: validate_video OUTPUT_DIR");
        std::filesystem::path dir(argv[1]);
        std::filesystem::create_directories(dir);
        VEliteTop top;
        auto *root = top.rootp;
        uint8_t expected[16384] = {};
        std::vector<uint8_t> image(640 * 480);
        top.io_input = 0;
        top.io_input2 = 0;
        top.reset = 1;
        auto tick = [&]() {
            top.clk = 0;
            top.eval();
            top.clk = 1;
            top.eval();
        };
        for (int i = 0; i < 16; ++i)
            tick();
        top.reset = 0;
        unsigned presentations = 0, complete = 0, audioMax = 0;
        uint64_t checked = 0;
        auto dumpRam = [&](const std::string &name) {
            std::ofstream ram(dir / (name + ".ram"), std::ios::binary);
            if (!ram)
                throw std::runtime_error("cannot create RAM artifact");
            for (int a = 0; a < 32768; ++a) {
                unsigned char byte = 0;
                int w = a / 4;
                switch (a % 4) {
                case 0:
                    byte = root->EliteTop__DOT__soc_1__DOT__core_ram_symbol0[w];
                    break;
                case 1:
                    byte = root->EliteTop__DOT__soc_1__DOT__core_ram_symbol1[w];
                    break;
                case 2:
                    byte = root->EliteTop__DOT__soc_1__DOT__core_ram_symbol2[w];
                    break;
                case 3:
                    byte = root->EliteTop__DOT__soc_1__DOT__core_ram_symbol3[w];
                    break;
                }
                ram.put(char(byte));
            }
        };
        for (uint64_t cycle = 0; cycle < 12000000; ++cycle) {
            top.clk = 0;
            top.eval();
            if (top.io_presented) {
                ++presentations;
                for (int i = 0; i < 16384; ++i)
                    expected[i] = root->EliteTop__DOT__soc_1__DOT__core_render__DOT__fb[i];
                dumpRam("present-" + std::to_string(presentations));
                if (presentations == 2)
                    top.io_input2 = 16;
                if (presentations == 3)
                    top.io_input2 = 0;
            }
            if (top.io_trap)
                throw std::runtime_error("integrated CPU trapped");
            audioMax = std::max(audioMax, unsigned(top.io_audioSample));
            if (top.io_active) {
                unsigned x = top.io_x, y = top.io_y;
                if (x >= 640 || y >= 480)
                    throw std::runtime_error("active coordinate outside VGA raster");
                unsigned pixel = 0;
                if (presentations && x >= 128 && x < 512 && y >= 48 && y < 432)
                    pixel = expected[((y - 48) / 3) * 128 + (x - 128) / 3];
                if (top.io_r != pixel || top.io_g != pixel || top.io_b != pixel)
                    throw std::runtime_error("RGB mismatch at x=" + std::to_string(x) +
                                             " y=" + std::to_string(y) +
                                             " presentation=" + std::to_string(presentations));
                image[y * 640 + x] = pixel ? 255 : 0;
                ++checked;
                if (x == 639 && y == 479 && presentations) {
                    ++complete;
                    std::ofstream frame(dir / ("video-" + std::to_string(complete) + ".pgm"),
                                        std::ios::binary);
                    frame << "P5\n640 480\n255\n";
                    frame.write(reinterpret_cast<const char *>(image.data()), image.size());
                    if (presentations >= 5)
                        break;
                }
            }
            top.clk = 1;
            top.eval();
        }
        if (presentations < 5 || complete < 4 || audioMax == 0 || top.io_dbg != 0xCAFE)
            throw std::runtime_error("integrated capture incomplete or missing sound");
        dumpRam("final");
        // Reset must clear presentation validity; uninitialized scanout banks
        // must not leak pixels while the CPU boots again.
        top.reset = 1;
        top.io_input2 = 0;
        for (int i = 0; i < 16; ++i)
            tick();
        top.reset = 0;
        bool reboot = false;
        for (uint64_t cycle = 0; cycle < 2000000; ++cycle) {
            top.clk = 0;
            top.eval();
            if (top.io_presented) {
                reboot = true;
                break;
            }
            if (top.io_r || top.io_g || top.io_b || top.io_trap)
                throw std::runtime_error("reset leaked stale video or trapped");
            top.clk = 1;
            top.eval();
        }
        if (!reboot || top.io_dbg != 0xCAFE)
            throw std::runtime_error("integrated SoC did not reboot");
        dumpRam("reboot");
        std::ofstream result(dir / "capture.json");
        result << "{\"passed\":true,\"presentations\":" << presentations
               << ",\"complete_video_frames\":" << complete
               << ",\"checked_active_pixels\":" << checked << ",\"audio_max\":" << audioMax
               << ",\"reboot\":true}\n";
        std::cout << "Integrated RGB/input/audio/reset capture passed; " << checked
                  << " active pixels checked\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
