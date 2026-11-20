// Observe native encoding/projection and exercise lossless raw-cell persistence.
#include "bbc_disc_medium.h"
#include "bbc_disc_payload.h"
#include <fstream>
#include <iostream>
#include <iterator>

int main(int argc, char **argv) {
    try {
        if (argc != 4)
            throw std::runtime_error("Expected SSD input, raw FM output and payload projection");
        std::ifstream source(argv[1], std::ios::binary);
        if (!source)
            throw std::runtime_error("Missing SSD input");
        std::vector<uint8_t> image{std::istreambuf_iterator<char>(source), {}};
        const auto cells = bbc_fm::encode_ssd(image);
        unsigned payloadBytes = 0;
        for (unsigned cell = 0; cell < bbc_fm::CellCount; ++cell) {
            const auto address = bbc_fm::payload_address(cell);
            if (address && *address != payloadBytes++)
                throw std::runtime_error("Payload observation reordered standard sectors");
        }
        if (payloadBytes != bbc_fm::SsdBytes ||
            bbc_fm::payload_address(bbc_fm::CellCount).has_value())
            throw std::runtime_error("Payload observation includes non-payload cells");
        const auto bytes = bbc_fm::serialize_cells(cells);
        if (bbc_fm::decode_cells(bytes) != cells)
            throw std::runtime_error("Encoded medium did not survive serialization");
        auto arbitrary = cells;
        for (unsigned cell = 0; cell < arbitrary.size(); ++cell)
            arbitrary[cell] = uint16_t(cell);
        if (bbc_fm::decode_cells(bbc_fm::serialize_cells(arbitrary)) != arbitrary)
            throw std::runtime_error("Raw persistence lost clock/data bits");
        auto medium = BbcDiscMedium::from_ssd(argv[1]);
        if (medium.size() != cells.size())
            throw std::runtime_error("Incorrect physical medium size");
        for (unsigned cell = 0; cell < cells.size(); ++cell) {
            if (medium.at(cell) != cells[cell])
                throw std::runtime_error("Backing store differs from encoded input");
            medium.set(cell, arbitrary[cell]);
        }
        const std::filesystem::path saved = std::string(argv[2]) + ".roundtrip";
        medium.save_raw(saved);
        const auto restored = BbcDiscMedium::from_raw(saved);
        for (unsigned cell = 0; cell < cells.size(); ++cell)
            if (restored.at(cell) != arbitrary[cell])
                throw std::runtime_error("File restore lost physical clock/data bits");
        std::filesystem::remove(saved);
        auto damaged = cells;
        damaged[47] ^= 0x0100;
        if (bbc_fm::project_payloads(damaged) != bbc_fm::project_payloads(cells) ||
            bbc_fm::serialize_cells(damaged) == bytes)
            throw std::runtime_error("Payload projection must not stand in for raw persistence");
        auto damagedMedium = BbcDiscMedium::from_ssd(argv[1]);
        damagedMedium.set(47, damaged[47]);
        damagedMedium.save_raw(saved);
        damagedMedium.save_projection(std::string(saved) + ".ssd");
        if (BbcDiscMedium::from_raw(saved).at(47) != damaged[47] ||
            BbcDiscMedium::from_ssd(std::string(saved) + ".ssd").at(47) == damaged[47])
            throw std::runtime_error("Restore repaired a damaged physical cell");
        std::filesystem::remove(saved);
        std::filesystem::remove(std::string(saved) + ".ssd");
        {
            std::ofstream truncated(saved, std::ios::binary);
            truncated.put(0);
        }
        bool rejectedRaw = false;
        try {
            BbcDiscMedium::from_raw(saved);
        } catch (const std::runtime_error &) {
            rejectedRaw = true;
        }
        std::filesystem::remove(saved);
        if (!rejectedRaw)
            throw std::runtime_error("Truncated raw medium accepted");
        for (unsigned size : {0U, 257U, bbc_fm::SsdBytes + 1}) {
            bool rejected = false;
            try {
                bbc_fm::encode_ssd(std::vector<uint8_t>(size));
            } catch (const std::runtime_error &) {
                rejected = true;
            }
            if (!rejected)
                throw std::runtime_error("Invalid SSD input accepted");
        }
        std::ofstream raw(argv[2], std::ios::binary), flat(argv[3], std::ios::binary);
        const auto payload = bbc_fm::project_payloads(cells);
        raw.write(reinterpret_cast<const char *>(bytes.data()), bytes.size());
        flat.write(reinterpret_cast<const char *>(payload.data()), payload.size());
        if (!raw || !flat)
            throw std::runtime_error("Failed to capture encoded medium");
        std::cout << "250000 cells encoded; all 65536 clock/data words survive persistence\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
