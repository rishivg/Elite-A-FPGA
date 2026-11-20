"""Encode shared SSD input as standard FM clock/data cells.

Each little-endian 16-bit word contains a clock byte above a data byte. This
module constructs input media; it never imports reference CPU or device state.
"""

import sys
from array import array
from collections.abc import Iterable

TRACKS = 80
SECTORS_PER_TRACK = 10
SECTOR_BYTES = 256
CELLS_PER_TRACK = 3125
SECTOR_CELLS = 310
FIRST_PAYLOAD_CELL = 47
SSD_BYTES = TRACKS * SECTORS_PER_TRACK * SECTOR_BYTES
FM_BYTES = TRACKS * CELLS_PER_TRACK * 2


def crc16(data: Iterable[int]) -> int:
    """IBM FM CRC, including the address mark, with initial value FFFF."""
    value = 0xFFFF
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (0x1021 if value & 0x8000 else 0)) & 0xFFFF
    return value


def encode_standard_ssd(image: bytes) -> bytes:
    """Encode sector-aligned SSD bytes, padding omitted trailing sectors with zero."""
    if not image or len(image) > SSD_BYTES or len(image) % SECTOR_BYTES:
        raise ValueError("Expected nonempty sector-aligned SSD input within 80 tracks")
    image = image.ljust(SSD_BYTES, b"\0")
    words = array("H")
    for track in range(TRACKS):
        cells = [0xFFFF] * 16 + [0xFF00] * 6
        for sector in range(SECTORS_PER_TRACK):
            header = bytes([0xFE, track, 0, sector, 1])
            checksum = crc16(header)
            cells += [0xC7FE] + [0xFF00 | byte for byte in header[1:]]
            cells += [0xFF00 | (checksum >> 8), 0xFF00 | (checksum & 255)]
            cells += [0xFFFF] * 11 + [0xFF00] * 6 + [0xC7FB]
            address = (track * SECTORS_PER_TRACK + sector) * SECTOR_BYTES
            payload = image[address : address + SECTOR_BYTES]
            cells += [0xFF00 | byte for byte in payload]
            checksum = crc16(bytes([0xFB]) + payload)
            cells += [0xFF00 | (checksum >> 8), 0xFF00 | (checksum & 255)]
            if sector != SECTORS_PER_TRACK - 1:
                cells += [0xFFFF] * 21 + [0xFF00] * 6
        if len(cells) > CELLS_PER_TRACK:
            raise ValueError("Standard FM track overrun")
        words.extend(cells + [0xFFFF] * (CELLS_PER_TRACK - len(cells)))
    if sys.byteorder != "little":
        words.byteswap()
    return words.tobytes()


def project_standard_payloads(cells: bytes) -> bytes:
    """Observe payload slots in a standard layout, retaining raw cells separately.

    This projection discards clocks, headers and CRCs. It is unsuitable as a
    saved controller medium or for reconstructing damaged/nonstandard tracks.
    """
    if len(cells) != FM_BYTES:
        raise ValueError("Expected 80 complete standard FM tracks")
    return bytes(
        cells[2 * (track * CELLS_PER_TRACK + FIRST_PAYLOAD_CELL + sector * SECTOR_CELLS + byte)]
        for track in range(TRACKS)
        for sector in range(SECTORS_PER_TRACK)
        for byte in range(SECTOR_BYTES)
    )
