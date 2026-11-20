// Actual parent clock/decoder/strobes and actual CPU NMI detector; no state injection.
#include "VDeviceEpochProbe.h"
#include "bbc_disc_payload.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
int main(int argc, char **argv) {
    try {
        if (argc != 4 && argc != 5)
            throw std::runtime_error(
                "Expected fixture, SSD, master multiplier and optional case count");
        const unsigned multiplier = std::stoul(argv[3]);
        const unsigned expectedCases = argc == 5 ? std::stoul(argv[4]) : 7;
        if (multiplier != 1 && multiplier != 3)
            throw std::runtime_error("Invalid multiplier");
        std::ifstream disk(argv[2], std::ios::binary), fixture(argv[1]);
        if (!disk || !fixture)
            throw std::runtime_error("Missing fixture or SSD");
        std::vector<uint8_t> payload{std::istreambuf_iterator<char>(disk), {}};
        const auto initialImage = bbc_fm::encode_ssd(payload);
        std::string marker, name;
        unsigned id, count, cases = 0, observations = 0;
        while (fixture >> marker >> id >> name >> count) {
            if (marker != "case" || !count)
                throw std::runtime_error("Bad fixture header");
            auto image = initialImage;
            VDeviceEpochProbe v;
            v.reset = 1;
            v.address = 0x2000;
            v.data = 0;
            v.write = 0;
            v.storage = 0;
            auto evaluate = [&]() {
                v.clk = 0;
                v.eval();
                const auto cell = image.at(v.storageAddress);
                if (v.storage != cell) {
                    v.storage = cell;
                    v.eval();
                }
            };
            auto commit = [&]() {
                v.clk = 1;
                v.eval();
            };
            // Exact production capture reset: 256 base clocks, with every master edge executed.
            for (unsigned i = 0; i < 256 * multiplier; i++) {
                evaluate();
                commit();
            }
            v.reset = 0;
            unsigned clock = 0, edges = 0;
            for (unsigned row = 0; row < count; row++) {
                unsigned cycle, address, data, status, nmi, expectedEdges;
                char action;
                if (!(fixture >> cycle >> action >> address >> data >> status >> nmi >>
                      expectedEdges) ||
                    cycle <= 16 || address > 7 || (action != 'R' && action != 'W' && action != 'P'))
                    throw std::runtime_error("Malformed fixture row");
                const unsigned target = (cycle - 16) * 16 * multiplier;
                if (target <= clock)
                    throw std::runtime_error("Unordered fixture");
                unsigned returned = 0, sampledLevel = 0;
                while (clock < target) {
                    ++clock;
                    // Present the request for its whole final slot; the actual
                    // parent alone chooses the accepted edge and FDC strobe.
                    const bool request = clock > target - 16 * multiplier && action != 'P';
                    const bool access = clock == target && action != 'P';
                    v.address = request ? 0xfe80 + address : 0x2000;
                    v.write = request && action == 'W';
                    v.data = data;
                    evaluate();
                    const bool expectedSlot = clock % (16 * multiplier) == 0;
                    if (v.slot != expectedSlot || v.accepted != expectedSlot ||
                        (access &&
                         ((action == 'W' && !v.fdcWrite) || (action == 'R' && !v.fdcRead)))) {
                        std::cout << "EPOCH_MISMATCH " << name << " master " << clock << '\n';
                        return 1;
                    }
                    if ((v.nmi && !v.previous) || v.eventPulse)
                        ++edges;
                    if (clock == target) {
                        returned = v.result;
                        sampledLevel = v.nmi;
                    }
                    if (v.storageWrite)
                        image.at(v.storageAddress) = v.storageData;
                    commit();
                }
                // Pin level after removing the accepted access; no extra clock is inserted.
                v.address = 0x2000;
                v.write = 0;
                evaluate();
                if ((action != 'W' && returned != data) || v.level != nmi ||
                    (action == 'P' && sampledLevel != nmi) || edges != expectedEdges) {
                    std::cout << "FDC_EPOCH_MISMATCH " << name << " cycle " << cycle << " return "
                              << returned << '/' << data << " nmi " << +v.level << '/' << nmi
                              << " edges " << edges << '/' << expectedEdges << '\n';
                    return 1;
                }
                ++observations;
            }
            ++cases;
        }
        if (!fixture.eof() || cases != expectedCases)
            throw std::runtime_error("Incomplete case inventory");
        std::cout << "FDC epoch: " << cases << " cold cases; " << observations
                  << " observations; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
