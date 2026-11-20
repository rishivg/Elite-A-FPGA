// Shared input-media encoding and byte-exact raw-cell serialization.
#pragma once
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace bbc_fm {
constexpr unsigned Tracks = 80, Sectors = 10, SectorBytes = 256, TrackCells = 3125;
constexpr unsigned SsdBytes = Tracks * Sectors * SectorBytes;
constexpr unsigned CellCount = Tracks * TrackCells;

inline uint16_t crc_byte(uint16_t crc, uint8_t byte) {
    crc ^= uint16_t(byte) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
        crc = uint16_t((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
    return crc;
}

inline std::vector<uint16_t> encode_ssd(std::vector<uint8_t> image) {
    if (image.empty() || image.size() > SsdBytes || image.size() % SectorBytes)
        throw std::runtime_error("Expected nonempty sector-aligned SSD within 80 tracks");
    image.resize(SsdBytes, 0);
    std::vector<uint16_t> cells;
    cells.reserve(CellCount);
    auto repeat = [&](uint16_t word, unsigned count) { cells.insert(cells.end(), count, word); };
    auto crc_words = [&](uint16_t crc) {
        cells.push_back(0xff00 | (crc >> 8));
        cells.push_back(0xff00 | (crc & 255));
    };
    for (unsigned track = 0; track < Tracks; ++track) {
        const unsigned start = cells.size();
        repeat(0xffff, 16);
        repeat(0xff00, 6);
        for (unsigned sector = 0; sector < Sectors; ++sector) {
            const uint8_t header[] = {0xfe, uint8_t(track), 0, uint8_t(sector), 1};
            uint16_t crc = 0xffff;
            for (unsigned byte = 0; byte < 5; ++byte) {
                cells.push_back((byte ? 0xff00 : 0xc700) | header[byte]);
                crc = crc_byte(crc, header[byte]);
            }
            crc_words(crc);
            repeat(0xffff, 11);
            repeat(0xff00, 6);
            cells.push_back(0xc7fb);
            crc = crc_byte(0xffff, 0xfb);
            const unsigned address = (track * Sectors + sector) * SectorBytes;
            for (unsigned byte = 0; byte < SectorBytes; ++byte) {
                cells.push_back(0xff00 | image[address + byte]);
                crc = crc_byte(crc, image[address + byte]);
            }
            crc_words(crc);
            if (sector != Sectors - 1) {
                repeat(0xffff, 21);
                repeat(0xff00, 6);
            }
        }
        if (cells.size() - start > TrackCells)
            throw std::runtime_error("FM track overrun");
        repeat(0xffff, TrackCells - (cells.size() - start));
    }
    return cells;
}

inline std::vector<uint16_t> decode_cells(const std::vector<uint8_t> &bytes) {
    if (bytes.size() != CellCount * 2)
        throw std::runtime_error("Expected 80 complete raw FM tracks");
    std::vector<uint16_t> cells(CellCount);
    for (unsigned cell = 0; cell < CellCount; ++cell)
        cells[cell] = bytes[cell * 2] | (uint16_t(bytes[cell * 2 + 1]) << 8);
    return cells;
}

inline std::vector<uint8_t> serialize_cells(const std::vector<uint16_t> &cells) {
    if (cells.size() != CellCount)
        throw std::runtime_error("Expected 80 complete raw FM tracks");
    std::vector<uint8_t> bytes(CellCount * 2);
    for (unsigned cell = 0; cell < CellCount; ++cell) {
        bytes[cell * 2] = uint8_t(cells[cell]);
        bytes[cell * 2 + 1] = uint8_t(cells[cell] >> 8);
    }
    return bytes;
}

// This observation discards clocks, headers and CRCs; saved DUT media must use raw cells.
inline std::vector<uint8_t> project_payloads(const std::vector<uint16_t> &cells) {
    if (cells.size() != CellCount)
        throw std::runtime_error("Expected 80 complete raw FM tracks");
    std::vector<uint8_t> bytes;
    bytes.reserve(SsdBytes);
    for (unsigned track = 0; track < Tracks; ++track)
        for (unsigned sector = 0; sector < Sectors; ++sector)
            for (unsigned byte = 0; byte < SectorBytes; ++byte)
                bytes.push_back(uint8_t(cells[track * TrackCells + 47 + sector * 310 + byte]));
    return bytes;
}
} // namespace bbc_fm
