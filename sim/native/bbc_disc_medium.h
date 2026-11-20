// Host backing store for the FPGA's physical FM clock/data cell interface.
#pragma once
#include "bbc_fm_media.h"
#include <filesystem>
#include <fstream>
#include <iterator>

class BbcDiscMedium {
    std::vector<uint16_t> cells;

    static std::vector<uint8_t> read(const std::filesystem::path &path) {
        std::ifstream file(path, std::ios::binary);
        if (!file)
            throw std::runtime_error("Missing disc medium: " + path.string());
        std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(file), {}};
        if (file.bad())
            throw std::runtime_error("Failed to read disc medium: " + path.string());
        return bytes;
    }

    static void write(const std::filesystem::path &path, const std::vector<uint8_t> &bytes) {
        std::ofstream file(path, std::ios::binary);
        file.write(reinterpret_cast<const char *>(bytes.data()), bytes.size());
        file.close();
        if (!file)
            throw std::runtime_error("Failed to write disc medium: " + path.string());
    }

    explicit BbcDiscMedium(std::vector<uint16_t> image) : cells(std::move(image)) {}

  public:
    static BbcDiscMedium from_ssd(const std::filesystem::path &path) {
        return BbcDiscMedium(bbc_fm::encode_ssd(read(path)));
    }

    // Restore raw words directly. Re-encoding the payload projection repairs
    // damaged clocks, headers and CRCs and changes subsequent controller input.
    static BbcDiscMedium from_raw(const std::filesystem::path &path) {
        return BbcDiscMedium(bbc_fm::decode_cells(read(path)));
    }

    uint16_t at(unsigned address) const { return cells.at(address); }
    void set(unsigned address, uint16_t word) { cells.at(address) = word; }
    unsigned size() const { return cells.size(); }
    void save_raw(const std::filesystem::path &path) const {
        write(path, bbc_fm::serialize_cells(cells));
    }
    void save_projection(const std::filesystem::path &path) const {
        write(path, bbc_fm::project_payloads(cells));
    }
};
