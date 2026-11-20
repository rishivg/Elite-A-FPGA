// Native driver for the 32 MHz and 96 MHz BBC machines.
#ifndef BBC_MACHINE_TYPE
#include "VBbcMachine.h"
#define BBC_MACHINE_TYPE VBbcMachine
#define BBC_MASTER_MULTIPLIER 1
#endif
#include "bbc_audio.h"
#include "bbc_disc_medium.h"
#include "bbc_frame_hash.h"
#include "verilated_save.h"
#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

int main(int argc, char **argv) {
    try {
        const auto started = std::chrono::steady_clock::now();
        if (argc > 5)
            throw std::runtime_error("Expected duration, keys, checkpoints and output arguments");
        uint64_t cycles = 64000000;
        if (argc > 1) {
            const std::string value = argv[1];
            if (value.empty() || !std::all_of(value.begin(), value.end(),
                                              [](unsigned char c) { return c >= '0' && c <= '9'; }))
                throw std::runtime_error("Duration must contain only decimal digits");
            cycles = std::stoull(value);
        }
        if (cycles > std::numeric_limits<uint64_t>::max() / BBC_MASTER_MULTIPLIER - 1280000)
            throw std::runtime_error("Duration exceeds the supported cycle range");
        if (cycles == 0)
            throw std::runtime_error("Duration must be positive");
        const std::filesystem::path output = argc > 4 ? argv[4] : ".";
        std::filesystem::create_directories(output);
        BBC_MACHINE_TYPE dut;
        static_assert(sizeof(dut.io_storageData) == sizeof(uint16_t),
                      "Raw FM backing store requires generated 16-bit storage ports");
        for (int i = 0; i < 4; i++)
            dut.io_keys[i] = 0;
        dut.io_analogue = 0x8000800080008000ULL;
        dut.io_joystickButtons = 0;
        dut.io_warmReset = 0;
        dut.io_debugAddress = 0;
        dut.io_storageData = 0;
        const char *restorePath = std::getenv("BBC_LOAD_STATE");
        const char *savePath = std::getenv("BBC_SAVE_STATE");
        const char *inputDisc = std::getenv("BBC_DISC_IMAGE");
        const bool restoring = restorePath && *restorePath;
        // Restore the raw FM cells so saved writes and markers survive.
        const char *rawInput = std::getenv("BBC_DISC_FM_IMAGE");
        auto disc = restoring ? BbcDiscMedium::from_raw(std::string(restorePath) + ".fm")
                    : rawInput && *rawInput
                        ? BbcDiscMedium::from_raw(rawInput)
                        : BbcDiscMedium::from_ssd(inputDisc && *inputDisc ? inputDisc : "disc.ssd");
        auto storeMedium = [&](const std::filesystem::path &ssdPath) {
            auto rawPath = ssdPath;
            rawPath.replace_extension(".fm");
            disc.save_raw(rawPath);
            disc.save_projection(ssdPath);
        };
        struct KeyEvent {
            uint64_t cycle;
            unsigned col, row, down;
        };
        std::vector<KeyEvent> keys;
        if (argc > 2 && std::string(argv[2]) != "-") {
            std::ifstream input(argv[2]);
            KeyEvent event;
            std::string line, extra;
            if (!input)
                throw std::runtime_error("Missing keyboard schedule");
            while (std::getline(input, line)) {
                if (line.empty())
                    continue;
                std::istringstream row(line);
                if (!(row >> event.cycle >> event.col >> event.row >> event.down) || row >> extra ||
                    event.cycle >= cycles || event.col >= 16 || event.row >= 8 || event.down > 1)
                    throw std::runtime_error("Invalid matrix event");
                keys.push_back(event);
            }
        }
        size_t nextKey = 0;

        std::vector<uint64_t> restarts;
        const char *restartPath = std::getenv("BBC_RESTART_EVENTS");
        if (restartPath && *restartPath) {
            std::ifstream input(restartPath);
            std::string line;
            if (!input)
                throw std::runtime_error("Missing restart schedule");
            while (std::getline(input, line)) {
                if (line.empty() || line.find_first_not_of("0123456789") != std::string::npos)
                    throw std::runtime_error("Invalid restart event integer");
                const uint64_t event = std::stoull(line);
                if (event < 256 || event >= cycles || event % 16 ||
                    (!restarts.empty() && event <= restarts.back()))
                    throw std::runtime_error("Invalid restart event time");
                restarts.push_back(event);
            }
        }
        size_t nextRestart = 0;
        std::ofstream restartCapture(output / "restarts.tsv");
        if (!restartCapture)
            throw std::runtime_error("Cannot record restart inputs");
        struct AnalogueEvent {
            uint64_t cycle;
            std::array<unsigned, 4> channels;
            unsigned buttons;
        };
        std::vector<AnalogueEvent> analogueEvents;
        const char *analoguePath = std::getenv("BBC_ANALOGUE_EVENTS");
        if (analoguePath && *analoguePath) {
            std::ifstream input(analoguePath);
            std::string line;
            if (!input)
                throw std::runtime_error("Missing analogue schedule");
            while (std::getline(input, line)) {
                if (line.empty())
                    continue;
                std::istringstream row(line);
                std::array<std::string, 6> fields;
                std::string extra;
                for (auto &field : fields)
                    if (!(row >> field))
                        throw std::runtime_error("Incomplete analogue event");
                if (row >> extra)
                    throw std::runtime_error("Extra analogue event fields");
                std::array<uint64_t, 6> values;
                for (size_t i = 0; i < fields.size(); i++) {
                    if (fields[i].find_first_not_of("0123456789") != std::string::npos)
                        throw std::runtime_error("Invalid analogue event integer");
                    values[i] = std::stoull(fields[i]);
                }
                if (values[0] >= cycles || values[5] > 3 ||
                    (!analogueEvents.empty() && values[0] <= analogueEvents.back().cycle))
                    throw std::runtime_error("Invalid analogue event time/buttons");
                AnalogueEvent event;
                event.cycle = values[0];
                event.buttons = unsigned(values[5]);
                for (size_t i = 0; i < 4; i++) {
                    if (values[i + 1] > 65535)
                        throw std::runtime_error("Analogue channel outside 16 bits");
                    event.channels[i] = unsigned(values[i + 1]);
                }
                analogueEvents.push_back(event);
            }
        }
        size_t nextAnalogue = 0;

        struct Checkpoint {
            uint64_t cycle;
            std::string name;
        };
        std::vector<Checkpoint> checkpoints;
        if (argc > 3 && std::string(argv[3]) != "-") {
            std::ifstream input(argv[3]);
            Checkpoint checkpoint;
            std::string line, extra;
            std::unordered_set<std::string> names;
            if (!input)
                throw std::runtime_error("Missing checkpoint schedule");
            while (std::getline(input, line)) {
                if (line.empty())
                    continue;
                std::istringstream row(line);
                if (!(row >> checkpoint.cycle >> checkpoint.name) || row >> extra ||
                    checkpoint.cycle > cycles || !names.insert(checkpoint.name).second ||
                    !std::all_of(
                        checkpoint.name.begin(), checkpoint.name.end(),
                        [](unsigned char c) { return std::isalnum(c) || c == '_' || c == '-'; }))
                    throw std::runtime_error("Invalid checkpoint");
                checkpoints.push_back(checkpoint);
            }
            if (checkpoints.empty())
                throw std::runtime_error("Checkpoint schedule is empty");
        }
        for (size_t i = 1; i < keys.size(); i++)
            if (keys[i].cycle < keys[i - 1].cycle)
                throw std::runtime_error("Keys are unsorted");
        for (size_t i = 1; i < checkpoints.size(); i++)
            if (checkpoints[i].cycle < checkpoints[i - 1].cycle)
                throw std::runtime_error("Checkpoints are unsorted");
        size_t nextCheckpoint = 0;
        std::array<uint8_t, 1024 * 512 * 3> frame{}, complete{};
        uint64_t bootCycles = 0;
        constexpr uint64_t StateMagic = 0xbbc6502a00000102ULL + BBC_MASTER_MULTIPLIER - 1;
        if (restoring) {
            VerilatedRestore archive;
            archive.open(restorePath);
            uint64_t magic;
            archive >> magic;
            if (magic != StateMagic) {
                // VerilatedRestore's destructor expects the model trailer.
                // A different machine layout cannot be consumed safely just
                // to reach it. Reject before deserializing any DUT state.
                std::cerr << "Invalid RTL state schema\n" << std::flush;
                std::_Exit(1);
            }
            archive >> bootCycles >> dut;
            archive.close();
            std::ifstream image(std::string(restorePath) + ".rgb", std::ios::binary);
            image.read(reinterpret_cast<char *>(complete.data()), complete.size());
            if (image.gcount() != std::streamsize(complete.size()))
                throw std::runtime_error("Truncated saved scanout");
            frame = complete;
        }
        bool previousVsync = restoring && dut.io_vsync;
        uint64_t frames = 0, pixels = 0;
        BbcFrameHash fieldHash;
        std::ofstream fieldDigests(output / "field-hashes.tsv");
        std::ofstream psg(output / "psg.tsv"), frameTimes(output / "frames.tsv"),
            inputs(output / "inputs.tsv"), audio(output / "audio.u8", std::ios::binary);
        std::ofstream analogueCapture(output / "analogue.tsv");
        uint64_t instructions = 0, psgWrites = 0, fdcWrites = 0;
        const unsigned traceLimit =
            std::getenv("BBC_TRACE_LIMIT") ? std::stoul(std::getenv("BBC_TRACE_LIMIT")) : 0;
        std::ofstream trace(output / "boot.trace");
        std::ofstream bus(output / "bus.tsv");
        const bool traceBus = std::getenv("BBC_BUS_TRACE") != nullptr;
        const bool traceRam = std::getenv("BBC_RAM_TRACE") != nullptr;
        const bool capturePrefix = std::getenv("BBC_CAPTURE_PREFIX") != nullptr;
        if (capturePrefix && (restoring || (savePath && *savePath)))
            throw std::runtime_error("A cold prefix cannot restore or save a game snapshot");
        std::ofstream ramWrites;
        if (traceRam) {
            ramWrites.open(output / "ram-writes.tsv");
            if (!ramWrites)
                throw std::runtime_error("Cannot create RAM write trace");
        }
        const bool alignBoot = savePath && *savePath;
        const uint64_t requestedCycles = cycles;
        const uint64_t limit = cycles + (alignBoot ? 1280000 : 0);
        bool aligned = !alignBoot;
        auto capture = [&](const std::string &name) {
            storeMedium(output / (name + ".ssd"));
            std::ofstream ram(output / (name + ".ram"), std::ios::binary);
            for (unsigned address = 0; address < 65536; address++) {
                dut.io_debugAddress = address;
                dut.eval();
                ram.put(char(dut.io_debugData));
            }
            std::ofstream image(output / (name + ".ppm"), std::ios::binary);
            image << "P6\n256 248\n255\n";
            for (unsigned y = 0; y < 248; y++)
                for (unsigned x = 0; x < 256; x++)
                    image.write(
                        reinterpret_cast<const char *>(complete.data() + (y * 1024 + x * 2) * 3),
                        3);
            std::ofstream raw(output / (name + ".rgb"), std::ios::binary);
            raw.write(reinterpret_cast<const char *>(complete.data()), complete.size());
            std::ofstream video(output / (name + ".video.json"));
            video << "{\"control\":" << unsigned(dut.io_videoControl)
                  << ",\"unsupported\":" << unsigned(dut.io_unsupportedVideo) << ",\"registers\":[";
            for (unsigned i = 0; i < 18; i++)
                video << (i ? "," : "") << ((dut.io_videoRegisters[i / 4] >> (8 * (i % 4))) & 255);
            video << "],\"palette\":[";
            for (unsigned i = 0; i < 16; i++)
                video << (i ? "," : "") << ((dut.io_videoPalette >> (4 * i)) & 15);
            video << "],\"frames\":" << frames << "}\n";
        };
        // Fixture times use 32 MHz ticks. Step every master edge within each tick.
#ifdef BBC_IDLE_FALL
        const bool skipIdleFall = std::getenv("BBC_FULL_EVAL") == nullptr;
#endif
        for (uint64_t master = 0; master < limit * BBC_MASTER_MULTIPLIER; master++) {
#ifdef BBC_IDLE_FALL
            const bool previousReset = dut.reset, previousWarmReset = dut.io_warmReset;
            const auto previousKey = nextKey, previousAnalogue = nextAnalogue;
#endif
            const uint64_t cycle = master / BBC_MASTER_MULTIPLIER;
            const bool endpoint = master % BBC_MASTER_MULTIPLIER == BBC_MASTER_MULTIPLIER - 1;
            if (master % BBC_MASTER_MULTIPLIER == 0) {
                dut.io_warmReset =
                    nextRestart < restarts.size() && restarts[nextRestart] <= cycle + 1;
                if (dut.io_warmReset)
                    restartCapture << restarts[nextRestart++] << "\n";
            }
            while (master % BBC_MASTER_MULTIPLIER == 0 && nextCheckpoint < checkpoints.size() &&
                   checkpoints[nextCheckpoint].cycle == cycle)
                capture(checkpoints[nextCheckpoint++].name);
            while (nextKey < keys.size() && keys[nextKey].cycle <= cycle + 1) {
                const auto &key = keys[nextKey++];
                unsigned bit = key.col * 8 + key.row;
                if (key.down)
                    dut.io_keys[bit / 32] |= uint32_t(1) << (bit % 32);
                else
                    dut.io_keys[bit / 32] &= ~(uint32_t(1) << (bit % 32));
                inputs << key.cycle << "\t" << key.col << "\t" << key.row << "\t" << key.down
                       << "\n";
            }
            while (nextAnalogue < analogueEvents.size() &&
                   analogueEvents[nextAnalogue].cycle <= cycle + 1) {
                const auto &event = analogueEvents[nextAnalogue++];
                uint64_t value = 0;
                analogueCapture << event.cycle;
                for (size_t i = 0; i < 4; i++) {
                    value |= uint64_t(event.channels[i]) << (16 * i);
                    analogueCapture << "\t" << event.channels[i];
                }
                dut.io_analogue = value;
                dut.io_joystickButtons = event.buttons;
                analogueCapture << "\t" << event.buttons << "\n";
            }
            dut.reset = !restoring && cycle < 256;
            dut.clk = 0;
#ifdef BBC_IDLE_FALL
            // Skip eval on a quiet fall when only the edge detector uses the clock.
            if (skipIdleFall && master != 0 && dut.reset == previousReset &&
                dut.io_warmReset == previousWarmReset && nextKey == previousKey &&
                nextAnalogue == previousAnalogue) {
                dut.rootp->__Vtrigprevexpr___TOP__clk__0 = 0;
            } else {
                dut.eval();
            }
#else
            dut.eval();
#endif
            const uint16_t storageData =
                dut.io_storageAddress < disc.size() ? disc.at(dut.io_storageAddress) : 0;
            // Settle the storage port only when its input word changes.
            if (dut.io_storageData != storageData) {
                dut.io_storageData = storageData;
                dut.eval();
            }
            if (dut.io_storageWrite && !dut.reset && dut.io_storageAddress < disc.size())
                disc.set(dut.io_storageAddress, dut.io_storageWriteData);
            if (dut.io_cpuEnable && !dut.reset) {
                if (traceRam && dut.io_cpuWrite && dut.io_cpuAddress < 0x8000) {
                    ramWrites << cycle + 1 << "\t" << unsigned(dut.io_cpuAddress) << "\t"
                              << unsigned(dut.io_cpuData) << "\n";
                }
                if (traceBus && dut.io_cpuWrite && dut.io_cpuAddress >= 0xfe00) {
                    bus << (cycle + 1) << "\t" << unsigned(dut.io_cpuAddress) << "\t"
                        << unsigned(dut.io_cpuData) << "\t" << unsigned(dut.io_videoX) << "\t"
                        << unsigned(dut.io_videoY) << "\n";
                }
                if (dut.io_instruction) {
                    instructions++;
                    uint16_t pc = static_cast<uint16_t>(dut.io_pc - 1);
                    if (instructions < traceLimit)
                        trace << std::hex << pc << " " << unsigned(dut.io_accumulator) << " "
                              << unsigned(dut.io_indexX) << " " << unsigned(dut.io_indexY) << "\n";
                    if (pc == 0xffee)
                        std::cout << char(dut.io_accumulator) << std::flush;
                }
                if (dut.io_fdcWrite) {
                    fdcWrites++;
                    if (dut.io_fdcAddress != 4)
                        std::cerr << "FDC write " << unsigned(dut.io_fdcAddress) << " " << std::hex
                                  << unsigned(dut.io_fdcWriteData) << std::dec << "\n";
                }
            }
            if (dut.io_psgWrite && !dut.reset) {
                psgWrites++;
                psg << (cycle + 1) << "\t" << unsigned(dut.io_psgData) << "\n";
            }
            // Write blanking pixels too, so a shorter display clears the old HUD.
            if (!dut.reset && dut.io_pixel && dut.io_videoX < 1024 && dut.io_videoY < 512) {
                size_t offset = (dut.io_videoY * 1024 + dut.io_videoX) * 3;
                frame[offset] = (dut.io_rgb & 1) ? 255 : 0;
                frame[offset + 1] = (dut.io_rgb & 2) ? 255 : 0;
                frame[offset + 2] = (dut.io_rgb & 4) ? 255 : 0;
                pixels++;
            }
            const bool endpointVsync = dut.io_videoVsyncEndpoint;
            dut.clk = 1;
            dut.eval();
            if (endpoint && bbc_audio_interval_complete(bootCycles + cycle) && !dut.reset)
                audio.put(char(dut.io_audioSample));
            const bool verticalEdge = endpointVsync && !previousVsync && !dut.reset;
            if (verticalEdge) {
                complete = frame;
                frames++;
                frameTimes << (cycle + 1) << "\n";
                fieldDigests << (cycle + 1) << " " << fieldHash.digest(complete) << "\n";
                pixels = 0;
            }
            previousVsync = endpointVsync;
            if (alignBoot && cycle + 1 >= requestedCycles && verticalEdge) {
                cycles = cycle + 1;
                aligned = true;
                break;
            }
            if (endpoint && cycle % 32000000 == 31999999) {
                std::cerr << "Second " << (cycle + 1) / 32000000 << " PC=$" << std::hex
                          << unsigned(dut.io_pc) << std::dec << " name=";
                for (unsigned address = 0x350; address < 0x357; address++) {
                    dut.io_debugAddress = address;
                    dut.eval();
                    std::cerr << char(dut.io_debugData);
                }
                std::cerr << " video=$" << std::hex << unsigned(dut.io_videoControl) << std::dec
                          << " frames=" << frames << "\n";
            }
        }
        if (traceRam) {
            ramWrites.flush();
            if (!ramWrites)
                throw std::runtime_error("Cannot finish RAM write trace");
        }
        if (!aligned)
            throw std::runtime_error("No vertical boundary after original-game boot");
        std::cout << "\nInstructions=" << instructions << " PC=$" << std::hex << unsigned(dut.io_pc)
                  << std::dec << " PSG writes=" << psgWrites << " FDC writes=" << fdcWrites
                  << " frames=" << frames << "\n";
        while (nextCheckpoint < checkpoints.size() && checkpoints[nextCheckpoint].cycle == cycles)
            capture(checkpoints[nextCheckpoint++].name);
        capture("boot");
        storeMedium(output / "disc-output.ssd");
        if (!capturePrefix && !restoring && !keys.empty()) {
            const unsigned addresses[] = {0x350, 0x351, 0x352, 0x353, 0x354, 0x355, 0x356,
                                          0x361, 0x362, 0x363, 0x364, 0x365, 0x87,  0x8e};
            const unsigned values[] = {'N', 'E', 'W',  'C',  'O', 'M', 'E',
                                       0,   0,   0x13, 0x88, 60,  8,   0xff};
            for (unsigned i = 0; i < 14; i++) {
                dut.io_debugAddress = addresses[i];
                dut.eval();
                if (i == 13 ? !(dut.io_debugData & 128) : dut.io_debugData != values[i])
                    throw std::runtime_error("Released-game boot did not reach the expected "
                                             "playable docked status screen");
            }
        }
        if (savePath && *savePath) {
            VerilatedSave archive;
            archive.open(savePath);
            archive << StateMagic << (bootCycles + cycles) << dut;
            archive.close();
            storeMedium(std::string(savePath) + ".ssd");
            std::ofstream savedImage(std::string(savePath) + ".rgb", std::ios::binary);
            savedImage.write(reinterpret_cast<const char *>(complete.data()), complete.size());
        }
        const bool completed = !capturePrefix && instructions > 10000 && nextKey == keys.size() &&
                               nextRestart == restarts.size() &&
                               nextAnalogue == analogueEvents.size() &&
                               nextCheckpoint == checkpoints.size() && frames >= 2;
        std::ofstream summary(output / "summary.json");
        summary << "{\"completed\":" << (completed ? "true" : "false")
                << ",\"duration_cycles\":" << cycles
                << ",\"clock_hz\":32000000,\"restored_own_boot_cycles\":" << bootCycles
                << ",\"frames\":" << frames << ",\"checkpoints\":" << nextCheckpoint
                << ",\"inputs\":" << nextKey << ",\"analogue_inputs\":" << nextAnalogue
                << ",\"psg_writes\":" << psgWrites;
        if (capturePrefix)
            summary << ",\"capture_scope\":\"cold-prefix\"";
        summary << "}\n";
        const double elapsed =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        std::cerr << "Simulation rate: " << cycles / elapsed / 1e6 << " million master cycles/s\n";
        return completed ? 0 : 1;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}
