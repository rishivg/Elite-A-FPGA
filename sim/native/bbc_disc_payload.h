// Lossy standard-sector observations for existing public disc-write fixtures.
#pragma once
#include "bbc_fm_media.h"
#include <optional>

namespace bbc_fm {
inline std::optional<unsigned> payload_address(unsigned cellAddress) {
    if (cellAddress >= CellCount)
        return std::nullopt;
    const unsigned track = cellAddress / TrackCells, cell = cellAddress % TrackCells;
    if (cell < 47)
        return std::nullopt;
    const unsigned relative = cell - 47, sector = relative / 310, byte = relative % 310;
    if (sector >= Sectors || byte >= SectorBytes)
        return std::nullopt;
    return (track * Sectors + sector) * SectorBytes + byte;
}
} // namespace bbc_fm
