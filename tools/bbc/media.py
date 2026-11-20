"""Declared commander-file inputs for normal guest DFS loading."""

from __future__ import annotations

from tools.validation.common import ROOT, digest, write_json
from tools.validation.setup import disc_files

PRESETS = {
    "rich-leesti": {"credits_tenths": 100000000, "coordinates": [13, 186]},
    "galaxy-eight": {
        "credits_tenths": 100000000,
        "coordinates": [1, 45],
        "galaxy": 7,
        "galactic_hyperdrive": True,
        "legal_status": 128,
        "delivery_reward_tenths": 5250,
        "delivery_destination": [6, 143],
    },
}


def checksum(data):
    """Elite-A CHECK: SEC, ADC previous byte, EOR current byte, backwards."""
    if len(data) < 76:
        raise ValueError("Commander input is shorter than the Elite-A data block")
    accumulator, carry = 73, 1
    for index in range(73, 0, -1):
        total = accumulator + data[index - 1] + carry
        accumulator, carry = total & 255, int(total > 255)
        accumulator ^= data[index]
    return accumulator


def image(released, preset):
    if preset not in PRESETS:
        raise ValueError(f"Unknown commander media preset: {preset}")
    if len(released) > 204800 or len(released) < 512:
        raise ValueError("Expected a single-sided 80-track DFS input image")
    files = disc_files(released)
    source = files.get("2.T", b"")
    marker = source.find(b"NEWCOME\r")
    if marker < 0 or source.find(b"NEWCOME\r", marker + 1) != -1:
        raise ValueError("Released default commander is missing or ambiguous")
    data = bytearray(source[marker + 8 : marker + 84])
    if (
        len(data) != 76
        or int.from_bytes(data[9:13], "big") != 5000
        or data[13] != 60
        or checksum(data) != data[75]
        or data[74] != data[75] ^ 169
    ):
        raise ValueError("Released default commander has an unexpected format")
    settings = PRESETS[preset]
    data[9:13] = settings["credits_tenths"].to_bytes(4, "big")
    data[1:3] = bytes(settings["coordinates"])
    if "galaxy" in settings:
        # Rotate the released seeds exactly as Ghy does. The input commander
        # starts at system 0 in galaxy 8; it does not claim seven played jumps.
        for _ in range(settings["galaxy"]):
            data[3:9] = bytes(((value << 1) | (value >> 7)) & 255 for value in data[3:9])
        data[15] = settings["galaxy"]
        data[45] = 255 if settings["galactic_hyperdrive"] else 0
        data[52] = settings["legal_status"]
        data[47:49] = settings["delivery_reward_tenths"].to_bytes(2, "little")
        data[49:51] = bytes(settings["delivery_destination"])
    data[75] = checksum(data)
    data[74] = data[75] ^ 169
    data.extend(bytes(256 - len(data)))
    result = bytearray(released) + bytearray(204800 - len(released))
    replaced = False
    for index in range(result[261] // 8):
        name = result[8 + index * 8 : 16 + index * 8]
        key = chr(name[7] & 127) + "." + name[:7].decode("ascii").strip()
        if key != "B.CONVERT":
            continue
        metadata = result[264 + index * 8 : 272 + index * 8]
        sector = metadata[7] + ((metadata[6] & 3) << 8)
        if not 2 <= sector < 800:
            raise ValueError("Utility's reusable sector lies outside the DFS medium")
        # The released catalogue is full. Reuse the unused conversion utility's
        # entry and first sector; executable and ship-file extents stay intact.
        result[8 + index * 8 : 16 + index * 8] = b"RICH   E"
        result[264 + index * 8 : 272 + index * 8] = bytes(
            [0, 0, 0, 0, 0, 1, sector >> 8, sector & 255]
        )
        result[sector * 256 : (sector + 1) * 256] = data
        replaced = True
        break
    if not replaced:
        raise ValueError("Released conversion utility is missing")
    actual = disc_files(result)
    if actual.get("E.RICH") != data or any(
        name != "B.CONVERT" and actual.get(name) != content for name, content in files.items()
    ):
        raise ValueError("Commander media construction changed a game or ship file")
    return bytes(result)


def prepare(preset):
    released = ROOT / "build/bbc/disc.ssd"
    directory = ROOT / "build/bbc/input-media"
    data = image(released.read_bytes(), preset)
    directory.mkdir(exist_ok=True)
    path = directory / (preset + ".ssd")
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    write_json(
        path.with_suffix(".json"),
        {
            "scope": "Synthetic commander file loaded through guest DFS; never machine RAM or a machine snapshot",
            "preset": preset,
            "settings": PRESETS[preset],
            "released_input_sha256": digest(released),
            "media_sha256": digest(path),
            "default_commander_source": "Released 2.T NEWCOME data block; 76 bytes padded to a 256-byte file",
            "removed_non_game_file": "B.CONVERT",
            "unchanged_game_and_ship_files": True,
        },
    )
    return path
