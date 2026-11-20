// Deterministic, read-only capture of the playable Soc. No SDL or host timing.
#include "VSoc.h"
#include "VSoc___024root.h"
#include "verilated.h"
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

struct Event {
    uint64_t cycle;
    uint32_t input, input2;
};
struct Checkpoint {
    uint64_t cycle;
    std::string name;
};
static uint64_t number(const std::string &s) {
    if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("expected unsigned decimal integer: " + s);
    size_t end;
    auto n = std::stoull(s, &end);
    if (end != s.size())
        throw std::runtime_error("invalid integer");
    return n;
}
static std::ofstream output(const std::filesystem::path &p, bool binary = false) {
    std::ofstream f(p, binary ? std::ios::binary : std::ios::out);
    if (!f)
        throw std::runtime_error("cannot create " + p.string());
    f.exceptions(std::ios::badbit | std::ios::failbit);
    return f;
}
int main(int argc, char **argv) {
    try {
        if (argc != 5)
            throw std::runtime_error(
                "usage: validate EVENTS.tsv CHECKPOINTS.tsv DURATION_CYCLES OUTPUT_DIR");
        uint64_t duration = number(argv[3]);
        if (!duration || duration > 4000000000ULL)
            throw std::runtime_error("duration outside 1..4 billion cycles");
        std::vector<Event> events;
        std::ifstream ef(argv[1]);
        uint64_t c, a, b;
        while (ef >> c >> a >> b) {
            if (a > UINT32_MAX || b > UINT32_MAX || c >= duration ||
                (!events.empty() && c <= events.back().cycle))
                throw std::runtime_error("invalid input schedule");
            events.push_back({c, uint32_t(a), uint32_t(b)});
        }
        if (!ef.eof() || events.empty() || events.front().cycle != 0)
            throw std::runtime_error("missing/malformed input schedule");
        std::vector<Checkpoint> checks;
        std::ifstream cf(argv[2]);
        std::string name;
        while (cf >> c >> name) {
            if (c >= duration ||
                name.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789_-") !=
                    std::string::npos ||
                (!checks.empty() && c <= checks.back().cycle))
                throw std::runtime_error("invalid checkpoint schedule");
            for (auto &cp : checks)
                if (cp.name == name)
                    throw std::runtime_error("duplicate checkpoint");
            checks.push_back({c, name});
        }
        if (!cf.eof() || checks.empty())
            throw std::runtime_error("missing/malformed checkpoint schedule");
        std::filesystem::path dir(argv[4]);
        std::filesystem::create_directories(dir);
        auto psg = output(dir / "psg.tsv"), reads = output(dir / "input_reads.tsv"),
             frames = output(dir / "frames.tsv");
        auto audio = output(dir / "audio.u8", true);
        VSoc top;
        auto *r = top.rootp;
        top.reset = 1;
        top.io_input = 0;
        top.io_input2 = 0;
        top.io_frameAck = 0;
        top.io_fbRdAddr = 0;
        auto edge = [&]() {
            top.clk = 0;
            top.eval();
            top.clk = 1;
            top.eval();
        };
        for (int i = 0; i < 16; ++i)
            edge();
        top.reset = 0;
        // Start the shared scenario clock at the first presented frame.
        uint64_t boot = 0;
        while (!top.io_frameReady && boot < 6000000) {
            edge();
            ++boot;
            if (top.io_trap)
                throw std::runtime_error("CPU trapped during boot");
        }
        if (!top.io_frameReady || top.io_dbg != 0xCAFE)
            throw std::runtime_error("DUT boot did not complete");
        uint8_t completeFb[16384];
        for (int i = 0; i < 16384; ++i)
            completeFb[i] = r->Soc__DOT__core_render__DOT__fb[i];
        size_t ei = 0, ci = 0;
        uint64_t frameCount = 0, lastFrame = 0;
        for (uint64_t cycle = 0; cycle < duration; ++cycle) {
            if (top.io_frameReady)
                for (int i = 0; i < 16384; ++i)
                    completeFb[i] = r->Soc__DOT__core_render__DOT__fb[i];
            if (ei < events.size() && events[ei].cycle == cycle) {
                top.io_input = events[ei].input;
                top.io_input2 = events[ei].input2;
                ++ei;
            }
            // Inspect arrays directly: reading through the framebuffer port advances
            // simulation time and changes game/audio timing.
            if (ci < checks.size() && checks[ci].cycle == cycle) {
                auto ram = output(dir / (checks[ci].name + ".ram"), true);
                for (int addr = 0; addr < 32768; ++addr) {
                    uint8_t byte = 0;
                    int word = addr / 4;
                    switch (addr % 4) {
                    case 0:
                        byte = r->Soc__DOT__core_ram_symbol0[word];
                        break;
                    case 1:
                        byte = r->Soc__DOT__core_ram_symbol1[word];
                        break;
                    case 2:
                        byte = r->Soc__DOT__core_ram_symbol2[word];
                        break;
                    case 3:
                        byte = r->Soc__DOT__core_ram_symbol3[word];
                        break;
                    }
                    ram.put(char(byte));
                }
                auto fb = output(dir / (checks[ci].name + ".pgm"), true);
                fb << "P5\n128 128\n255\n";
                for (int i = 0; i < 16384; ++i)
                    fb.put(completeFb[i] ? char(255) : char(0));
                ++ci;
            }
            top.io_frameAck = top.io_frameReady;
            if (top.io_frameReady) {
                frames << cycle << '\n';
                ++frameCount;
                lastFrame = cycle;
            }
            top.clk = 0;
            top.eval();
            if (r->Soc__DOT__core_psg_io_write)
                psg << cycle << '\t' << (r->Soc__DOT__cpu_mem_wdata & 255) << '\n';
            if (r->Soc__DOT__cpu_mem_valid && r->Soc__DOT__core_ready &&
                !r->Soc__DOT__cpu_mem_wstrb) {
                uint32_t addr = r->Soc__DOT__cpu_mem_addr;
                if (addr == 0x1000000C || addr == 0x10000058)
                    reads << cycle << '\t' << addr << '\t' << r->Soc__DOT__cpu_mem_rdata << '\n';
            }
            top.clk = 1;
            top.eval();
            // 500 kHz source samples, integer decimation from the declared 4 MHz clock.
            if (cycle % 8 == 7)
                audio.put(char(top.io_audioSample));
            if (top.io_trap)
                throw std::runtime_error("DUT CPU trapped at cycle " + std::to_string(cycle));
            if (cycle - lastFrame > 6000000)
                throw std::runtime_error("DUT stopped presenting frames");
        }
        if (ei != events.size() || ci != checks.size() || frameCount < 2 || top.io_dbg != 0xCAFE)
            throw std::runtime_error("incomplete DUT capture");
        auto summary = output(dir / "summary.json");
        summary << "{\"completed\":true,\"clock_hz\":4000000,\"duration_cycles\":" << duration
                << ",\"boot_cycles\":" << boot << ",\"frames\":" << frameCount
                << ",\"checkpoints\":" << ci << ",\"audio_samples\":" << duration / 8 << "}\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "capture failed: " << e.what() << '\n';
        return 2;
    }
}
