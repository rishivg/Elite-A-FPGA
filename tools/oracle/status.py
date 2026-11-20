"""Check demo legal-status bands, crimes, flight condition and equipment labels."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"


def parse_fw():
    text = FW_PATH.read_text()
    names = re.findall(r"LEGAL_NAMES\[3\]\s*=\s*\{([^}]*)\}", text)
    legal_names = re.findall(r'"([^"]*)"', names[0]) if names else []
    # legal_class(v) = v == 0 ? 0 : v < THRESH ? 1 : 2
    m = re.search(r"legal_class\(int v\)\s*\{\s*return\s+v == 0 \? 0 : v < (\d+)", text)
    thresh = int(m.group(1)) if m else None
    # the murder penalty: commit_crime(N) at the kill site
    m2 = re.search(r"commit_crime\((\d+)\)", text)
    crime = int(m2.group(1)) if m2 else None
    # condition band names
    cond = re.findall(r"condition_name\(void\)\s*\{(.*?)\n\}", text, re.S)
    cond_names = re.findall(r'return "([A-Z]+)"', cond[0]) if cond else []
    return legal_names, thresh, crime, cond_names


def legal_class(v: int, thresh: int) -> int:
    return 0 if v == 0 else 1 if v < thresh else 2


def commit_crime(v: int, n: int) -> int:
    return max(0, min(255, v + n))


def check() -> tuple[bool, str]:
    legal_names, thresh, crime, cond_names = parse_fw()

    if legal_names != ["CLEAN", "OFFENDER", "FUGITIVE"]:
        return False, f"LEGAL_NAMES = {legal_names}, expected Clean/Offender/Fugitive"
    if thresh != 50:
        return False, f"fugitive threshold = {thresh}, expected 50"
    if crime is None:
        return False, "could not find the commit_crime() murder penalty in fw"

    # 1. Band boundaries (the same five the CPU status_self_test checks).
    bands = {0: 0, 1: 1, thresh - 1: 1, thresh: 2, 255: 2}
    for v, want in bands.items():
        if legal_class(v, thresh) != want:
            return False, f"legal_class({v}) = {legal_class(v, thresh)}, expected {want}"

    # 2. Crime accumulation: a clean commander murdering innocents climbs
    #    Clean -> Offender -> Fugitive at the right number of kills.
    v = 0
    if legal_class(v, thresh) != 0:
        return False, "fresh commander is not Clean"
    v = commit_crime(v, crime)  # one murder
    if legal_class(v, thresh) != 1:
        return False, f"one murder ({v}) did not make us an Offender"
    kills = 1
    while legal_class(v, thresh) < 2:
        v = commit_crime(v, crime)
        kills += 1
        if kills > 100:
            return False, "never reached Fugitive"
    if v < thresh:
        return False, f"became Fugitive below threshold ({v})"
    # crossing happens at ceil(thresh/crime) murders
    expect_kills = -(-thresh // crime)
    if kills != expect_kills:
        return False, f"reached Fugitive after {kills} murders, expected {expect_kills}"

    # 3. Clamp at 255 (no wrap to a lower band).
    v = 250
    for _ in range(10):
        v = commit_crime(v, crime)
    if v != 255 or legal_class(v, thresh) != 2:
        return False, f"legal status did not clamp at 255 (got {v})"

    # 4. Condition band names present in the right order.
    if cond_names != ["RED", "YELLOW", "GREEN"]:
        return False, f"condition names = {cond_names}, expected RED/YELLOW/GREEN"

    # 5. The status screen lists the expected equipment labels (drift-catch).
    text = FW_PATH.read_text()
    start = text.index("draw_status(void)")
    block = text[start : text.index("static void market_step", start)]
    for label in (
        "PULSE LASER",
        "BEAM LASER",
        "MILITARY LASER",
        "MISSILES",
        "LARGE CARGO BAY",
        "ECM SYSTEM",
        "FUEL SCOOPS",
        "ESCAPE POD",
        "ENERGY BOMB",
        "ENERGY UNIT",
        "DOCK COMPUTER",
        "GAL HYPERDRIVE",
    ):
        if label not in block:
            return False, f"status screen is missing equipment label {label!r}"

    return True, (
        f"legal bands Clean/Offender(<{thresh})/Fugitive match fw; murder "
        f"+{crime} -> Offender after 1, Fugitive after {expect_kills}, clamps "
        f"at 255; condition RED/YELLOW/GREEN; 12 equipment labels present"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "status: " + msg)
