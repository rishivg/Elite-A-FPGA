"""Pinned inputs, artifact paths, and source-independent state decoding."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = ROOT / "build/validation"
LOCK = json.loads((HERE / "reference.lock.json").read_text())


def artifact_hashes(directory: Path) -> dict[str, str]:
    """Hash reference and DUT capture files using capture-relative paths."""
    return {
        str(path.relative_to(directory)): digest(path)
        for backend in ("reference", "dut")
        for path in sorted((directory / backend).glob("*"))
        if path.is_file()
    }


def digest(path: Path) -> str:
    # Keep memory bounded when hashing long raw audio recordings.
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_root(name: str) -> Path:
    source = LOCK["sources"][name]
    return CACHE / "vendor" / (source["repository"].split("/")[1] + "-" + source["commit"])


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def publish_latest(link: Path, directory: Path) -> None:
    """Atomically publish a report link relative to its final location."""
    if link.exists() and not link.is_symlink():
        raise FileExistsError(f"Expected a report symlink: {link}")
    with tempfile.TemporaryDirectory(dir=link.parent, prefix=f".{link.name}-") as staging:
        pending = Path(staging) / "link"
        pending.symlink_to(os.path.relpath(directory, link.parent), target_is_directory=True)
        pending.replace(link)


def listing_symbols(lines: list[str], overlay: str = "docked") -> dict[str, int]:
    """Resolve symbols within one original code overlay, with shared data fallback.

    The compiler listing concatenates several separately assembled programs.
    Repeated routine names must not accidentally select another overlay's code.
    """
    if overlay not in {"docked", "flight"}:
        raise ValueError(f"Unsupported reference overlay: {overlay}")
    marker = next((i for i, line in enumerate(lines) if f"S% workspace ({overlay})" in line), None)
    if marker is None:
        raise ValueError(f"Missing reference overlay: {overlay}")
    start = max(i for i in range(marker) if lines[i] == ".ZP")
    end = next((i for i in range(marker + 1, len(lines)) if lines[i] == ".ZP"), len(lines))

    def parse(section):
        result = {}
        for i, line in enumerate(section[:-1]):
            match = re.fullmatch(r"\.([\w%]+)", line)
            address = re.match(r"\s+([0-9A-F]{4})(?:\s|$)", section[i + 1])
            if match and address:
                result.setdefault(match[1], int(address[1], 16))
        return result

    symbols = parse(lines)
    symbols.update(parse(lines[start:end]))
    return symbols


@lru_cache(maxsize=4)
def _reference_symbols(listing: bytes, overlay: str) -> tuple:
    # Cache by full content, so changed bytes invalidate reuse even when file
    # timestamps are preserved. Parsing a listing for every checkpoint costs
    # much more than reading and comparing its immutable bytes.
    symbols = listing_symbols(listing.decode().splitlines(), overlay)
    required = [
        "NAME",
        "CASH",
        "QQ14",
        "QQ20",
        "QQ21",
        "GCNT",
        "FIST",
        "TALLY",
        "QQ0",
        "QQ1",
        "QQ2",
        "QQ3",
        "QQ4",
        "QQ6",
        "QQ7",
        "cmdr_type",
        "new_hold",
        "NOMSL",
        "QQ11",
        "QQ12",
        "DELTA",
        "ALPHA",
        "BETA",
    ]
    missing = set(required) - symbols.keys()
    if missing:
        raise ValueError(f"Missing reference symbols: {sorted(missing)}")
    return tuple(symbols.items())


def reference_symbols(overlay: str = "docked") -> dict[str, int]:
    # The listing belongs to the pinned source archive, not to the DUT. Return
    # a fresh dictionary so a caller cannot mutate the cached symbol table.
    listing = (source_root("elite_a") / "3-assembled-output/compile.txt").read_bytes()
    return dict(_reference_symbols(listing, overlay))


def firmware_symbols() -> dict[str, dict]:
    result = subprocess.run(
        ["riscv64-unknown-elf-nm", "-S", "--defined-only", str(ROOT / "build/fw/firmware.elf")],
        check=True,
        capture_output=True,
        text=True,
    )
    symbols = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 4:
            address, size, kind, name = parts
            if kind.lower() in {"b", "d", "r"}:
                symbols[name] = {"address": int(address, 16), "size": int(size, 16)}
    required = {
        "credits",
        "fuel",
        "cargo",
        "kills",
        "player_ship",
        "galaxy_seed",
        "frame_no",
        "p_alpha",
        "p_beta",
        "p_delta",
        "cmdr_slot",
        "cmdr_slot_valid",
        "market_mode",
        "chart_mode",
        "equip_mode",
        "shipyard_mode",
        "status_mode",
    }
    if not required <= symbols.keys():
        raise ValueError(f"Firmware symbols unavailable: {sorted(required - symbols.keys())}")
    for name, item in symbols.items():
        if item["address"] + item["size"] > 32768:
            raise ValueError(f"Firmware symbol outside SoC RAM: {name}")
    return symbols


def decode_reference(data: bytes, symbols: dict[str, int]) -> dict:
    if len(data) != 65536:
        raise ValueError("Reference RAM capture is truncated")

    def read(name, size=1, endian="little"):
        return int.from_bytes(data[symbols[name] : symbols[name] + size], endian)

    return {
        "commander_name": data[symbols["NAME"] : symbols["NAME"] + 8]
        .split(b"\r")[0]
        .split(b"\0")[0]
        .decode("ascii"),
        "credits_tenths": read("CASH", 4, "big"),
        "fuel_tenths_ly": read("QQ14"),
        "galaxy": read("GCNT"),
        "galaxy_seed_bytes": list(data[symbols["QQ21"] : symbols["QQ21"] + 6]),
        "cargo": list(data[symbols["QQ20"] : symbols["QQ20"] + 17]),
        "kills": read("TALLY", 2),
        "legal_status": read("FIST"),
        "missiles": read("NOMSL"),
        "ship_id": read("cmdr_type"),
        "ship_name": [
            "ADDER",
            "GECKO",
            "MORAY",
            "COBRA MK1",
            "IGUANA",
            "OPHIDIAN",
            "CHAMELEON",
            "COBRA MK3",
            "GHAVIAL",
            "FER-DE-LANCE",
            "MONITOR",
            "PYTHON",
            "BOA",
            "ANACONDA",
            "ASP MK2",
        ][read("cmdr_type")]
        if read("cmdr_type") < 15
        else "UNKNOWN",
        "current_coordinates": [read("QQ0"), read("QQ1")],
        # new_hold is equipment-adjusted capacity + 1. tnpr counts only
        # tonne canisters (commodities 0..12); precious cargo has separate limits.
        "free_hold": read("new_hold") - 1 - sum(data[symbols["QQ20"] : symbols["QQ20"] + 13]),
        "speed": read("DELTA"),
        "roll": read("ALPHA"),
        "pitch": read("BETA"),
        "view": read("QQ11"),
        "docked": bool(read("QQ12") & 128),
        "selected_system": {
            "economy": read("QQ3"),
            "government": read("QQ4"),
            "population": read("QQ6"),
            "productivity": read("QQ7", 2),
        },
        "commander_bytes": list(data[symbols["NAME"] : symbols["SVC"] + 3]),
    }


def decode_dut(data: bytes, symbols: dict[str, dict]) -> dict:
    if len(data) != 32768:
        raise ValueError("DUT RAM capture is truncated")

    def raw(name):
        item = symbols[name]
        return data[item["address"] : item["address"] + item["size"]]

    def read(name, signed=False):
        return int.from_bytes(raw(name), "little", signed=signed)

    source = (ROOT / "fw/main.c").read_text()
    commander = re.search(r'#define COMMANDER_NAME "([^"]+)"', source)
    hulls = re.search(r"static const ship_spec_t ships\[NSHIPS_SPEC\] = \{(.*?)\};", source, re.S)
    names = re.findall(r'\{\s*"([^"]+)"', hulls[1])
    state = {
        "commander_name": commander[1],
        "credits_tenths": read("credits"),
        "fuel_tenths_ly": read("fuel"),
        "galaxy": read("current_galaxy"),
        "galaxy_seed_bytes": list(raw("galaxy_seed")),
        "cargo": list(raw("cargo")),
        "kills": read("kills"),
        "legal_status": read("legal_status"),
        "missiles": read("missiles"),
        "ship_id": read("player_ship"),
        "ship_name": names[read("player_ship")],
        "free_hold": read("cargo_cap") - sum(raw("cargo")),
        "speed": read("p_delta"),
        "roll": read("p_alpha", True),
        "pitch": read("p_beta", True),
        "dock_animation_active": bool(read("dock_timer")),
        "mission": read("mission_state"),
        "save_valid": bool(read("cmdr_slot_valid")),
        "commander_bytes": list(raw("cmdr_slot")),
        "internal": {},
    }
    for name in [
        "frame_no",
        "chart_mode",
        "market_mode",
        "market_sel",
        "equip_mode",
        "equip_sel",
        "shipyard_mode",
        "yard_sel",
        "status_mode",
        "player_shields",
        "energy",
        "laser_heat",
        "target_sys",
        "current_sys",
        "hyper_flash",
        "missile_active",
        "mission_state",
        "kills",
        "legal_status",
        "dockit_active",
        "dying",
        "save_flash",
    ]:
        if name in symbols:
            state["internal"][name] = read(name, True)
    return state
