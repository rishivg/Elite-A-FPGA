// Execute a cold public prefix once, then fork its actual state for each history.
// Every branch inherits only DUT state reached by the same checked host accesses.
#include "VDeviceEpochProbe.h"
#include "bbc_disc_payload.h"
#include <cstdint>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

struct Observation {
    unsigned cycle, address, data, status, nmi, edges;
    char action;
    bool operator==(const Observation &other) const {
        return cycle == other.cycle && address == other.address && data == other.data &&
               status == other.status && nmi == other.nmi && edges == other.edges &&
               action == other.action;
    }
};
struct History {
    std::string name;
    std::vector<Observation> rows;
    std::vector<uint16_t> expectedMedia;
};

int main(int argc, char **argv) {
    try {
        if (argc != 5 && argc != 6)
            throw std::runtime_error("Expected fixture, SSD, master multiplier and case count");
        const unsigned multiplier = std::stoul(argv[3]), expectedCases = std::stoul(argv[4]);
        if ((multiplier != 1 && multiplier != 3) || !expectedCases)
            throw std::runtime_error("Invalid clock or case inventory");
        std::ifstream disk(argv[2], std::ios::binary), fixture(argv[1]);
        if (!disk || !fixture)
            throw std::runtime_error("Missing fixture or SSD");
        std::vector<uint8_t> payload{std::istreambuf_iterator<char>(disk), {}};
        auto image = bbc_fm::encode_ssd(payload);
        std::vector<History> histories;
        unsigned observations = 0;
        std::string marker, name;
        unsigned id, count;
        while (fixture >> marker >> id >> name >> count) {
            if (marker != "case" || id != histories.size() || !count)
                throw std::runtime_error("Bad fixture header");
            History history{name, {}};
            unsigned previous = 16;
            for (unsigned i = 0; i < count; ++i) {
                Observation row{};
                if (!(fixture >> row.cycle >> row.action >> row.address >> row.data >> row.status >>
                      row.nmi >> row.edges) ||
                    row.cycle <= previous || row.address > 7 || row.data > 255 ||
                    row.status > 255 || row.nmi > 1 ||
                    (row.action != 'R' && row.action != 'W' && row.action != 'P'))
                    throw std::runtime_error("Malformed fixture row");
                previous = row.cycle;
                history.rows.push_back(row);
            }
            observations += count;
            if (argc == 6) {
                std::ifstream media(std::string(argv[5]) + "/media-" + std::to_string(id) + ".bin",
                                    std::ios::binary);
                if (!media)
                    throw std::runtime_error("Missing original final medium");
                std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(media), {}};
                history.expectedMedia = bbc_fm::decode_cells(bytes);
            }
            histories.push_back(history);
        }
        if (!fixture.eof() || histories.size() != expectedCases)
            throw std::runtime_error("Incomplete case inventory");
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
        for (unsigned i = 0; i < 256 * multiplier; ++i) {
            evaluate();
            commit();
        }
        v.reset = 0;
        uint64_t clock = 0;
        unsigned edges = 0;
        auto replay = [&](const History &history, size_t begin, size_t end) {
            for (size_t i = begin; i < end; ++i) {
                const auto &row = history.rows[i];
                const uint64_t period = 16 * multiplier;
                const uint64_t target = uint64_t(row.cycle - 16) * period;
                unsigned returned = 0, sampledLevel = 0;
                while (clock < target) {
                    ++clock;
                    const bool request = clock > target - period && row.action != 'P';
                    const bool access = clock == target && row.action != 'P';
                    v.address = request ? 0xfe80 + row.address : 0x2000;
                    v.write = request && row.action == 'W';
                    v.data = row.data;
                    evaluate();
                    const bool expectedSlot = clock % period == 0;
                    if (v.slot != expectedSlot || v.accepted != expectedSlot ||
                        (access && ((row.action == 'W' && !v.fdcWrite) ||
                                    (row.action == 'R' && !v.fdcRead)))) {
                        std::cout << "EPOCH_MISMATCH " << history.name << " master " << clock
                                  << '\n';
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
                v.address = 0x2000;
                v.write = 0;
                evaluate();
                if ((row.action != 'W' && returned != row.data) || v.level != row.nmi ||
                    (row.action == 'P' && sampledLevel != row.nmi) || edges != row.edges) {
                    std::cout << "FDC_EPOCH_MISMATCH " << history.name << " cycle " << row.cycle
                              << " return " << returned << '/' << row.data << " nmi " << +v.level
                              << '/' << row.nmi << " edges " << edges << '/' << row.edges << '\n';
                    return 1;
                }
            }
            return 0;
        };
        std::function<int(const std::vector<size_t> &, size_t)> branches;
        branches = [&](const std::vector<size_t> &selected, size_t begin) {
            const auto &first = histories.at(selected.front());
            size_t common = first.rows.size();
            for (const auto index : selected) {
                const auto &history = histories.at(index);
                size_t length = begin;
                while (length < common && length < history.rows.size() &&
                       history.rows[length] == first.rows[length])
                    ++length;
                common = length;
            }
            if (replay(first, begin, common))
                return 1;
            std::vector<std::vector<size_t>> groups;
            for (const auto index : selected) {
                const auto &history = histories.at(index);
                if (history.rows.size() == common) {
                    if (!history.expectedMedia.empty() && image != history.expectedMedia) {
                        for (unsigned cell = 0; cell < image.size(); cell++)
                            if (image[cell] != history.expectedMedia[cell]) {
                                std::cout << "FDC_MEDIA_MISMATCH " << history.name << " cell "
                                          << cell << " value " << image[cell] << '/'
                                          << history.expectedMedia[cell] << '\n';
                                break;
                            }
                        return 1;
                    }
                    continue;
                }
                bool added = false;
                for (auto &group : groups) {
                    if (history.rows[common] == histories.at(group.front()).rows[common]) {
                        group.push_back(index);
                        added = true;
                        break;
                    }
                }
                if (!added)
                    groups.push_back({index});
            }
            if (groups.size() == 1)
                return branches(groups.front(), common);
            for (const auto &group : groups) {
                // Fork copies the complete simulator, medium and CPU edge
                // detector at this checked prefix. The parent stays unchanged.
                const pid_t child = fork();
                if (child < 0)
                    throw std::runtime_error("Cannot fork verified execution prefix");
                if (child == 0) {
                    const int result = branches(group, common);
                    std::cout.flush();
                    _exit(result);
                }
                int status;
                if (waitpid(child, &status, 0) != child || !WIFEXITED(status))
                    throw std::runtime_error("History branch did not finish");
                if (WEXITSTATUS(status))
                    return WEXITSTATUS(status);
            }
            return 0;
        };
        std::vector<size_t> selected;
        for (size_t i = 0; i < histories.size(); ++i)
            selected.push_back(i);
        if (const int result = branches(selected, 0))
            return result;
        std::cout << "FDC epoch: " << histories.size() << " cold cases; " << observations
                  << " observations; 0 differences\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
