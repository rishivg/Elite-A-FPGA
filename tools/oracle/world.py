"""Model demo police, asteroid fragments, cargo collection and fuel scooping."""

from __future__ import annotations

import re
from pathlib import Path

from . import status  # legal_class / commit_crime live with the status rules

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"


def parse_fw():
    text = FW_PATH.read_text()

    def def_int(name, default=None):
        match = re.search(r"#define\s+" + name + r"\s+Q?\(?(-?\d+)", text)
        return int(match.group(1)) if match else default

    # police_max(legal) = legal >= THRESH ? 2 : 1
    pm = re.search(r"police_max\(int legal\)\s*\{\s*return legal >= (\d+)", text)
    police_thresh = int(pm.group(1)) if pm else None
    # mine_splinters(tier) = tier >= T ? HI : LO
    ms = re.search(r"mine_splinters\(int tier\)\s*\{\s*return tier >= (\d+) \? (\d+) : (\d+)", text)
    mine = (int(ms.group(1)), int(ms.group(2)), int(ms.group(3))) if ms else None
    # crime weights at the kill site
    trader_crime = (
        int(m.group(1))
        if (m := re.search(r"ROLE_TRADER\)\s*commit_crime\((\d+)\)", text))
        else None
    )
    police_crime = (
        int(m.group(1))
        if (m := re.search(r"ROLE_POLICE\)\s*commit_crime\((\d+)\)", text))
        else None
    )
    cadence = def_int("POLICE_CADENCE")
    return {
        "police_thresh": police_thresh,
        "mine": mine,
        "trader_crime": trader_crime,
        "police_crime": police_crime,
        "cadence": cadence,
        # legal threshold reused from the status oracle
    }


def police_max(legal: int, thresh: int) -> int:
    return 2 if legal >= thresh else 1


def police_active(legal: int) -> bool:
    return legal > 0  # only a wanted commander draws the law


def mine_splinters(tier: int, mine) -> int:
    t, hi, lo = mine
    return hi if tier >= t else lo


def can_scoop(has_scoop: bool, cargo_used: int, cargo_cap: int) -> bool:
    return has_scoop and cargo_used < cargo_cap


def check() -> tuple[bool, str]:
    fw = parse_fw()
    _, legal_thresh, _, _ = status.parse_fw()  # legal_names, thresh, crime, cond

    for k in ("police_thresh", "mine", "trader_crime", "police_crime", "cadence"):
        if fw[k] is None:
            return False, f"could not parse {k} from firmware"

    # 1. Police heat by legal band (the CPU world_self_test checks these too).
    pt = fw["police_thresh"]
    if pt != legal_thresh:
        return False, f"police threshold {pt} != fugitive threshold {legal_thresh}"
    if police_active(0) is not False:
        return False, "police hunt a clean commander"
    if police_active(0) or not police_active(1):
        return False, "police-active gate wrong (clean=no, offender=yes)"
    if police_max(1, pt) != 1 or police_max(pt - 1, pt) != 1 or police_max(pt, pt) != 2:
        return False, "police_max band boundaries wrong"

    # 2. Murder weights: killing the law is worse than killing a trader, and both
    #    make a clean commander an Offender immediately.
    if not (fw["police_crime"] > fw["trader_crime"] > 0):
        return False, f"crime weights off: trader {fw['trader_crime']}, police {fw['police_crime']}"
    if status.legal_class(status.commit_crime(0, fw["trader_crime"]), legal_thresh) != 1:
        return False, "murdering one trader did not make us an Offender"
    if status.legal_class(status.commit_crime(0, fw["police_crime"]), legal_thresh) != 1:
        return False, "murdering one policeman did not make us an Offender"

    # 3. Mining yields more with a beam/mining laser than a pulse.
    mine = fw["mine"]
    if mine_splinters(1, mine) != mine[2] or mine_splinters(2, mine) != mine[1]:
        return False, "mine_splinters tier gating wrong"
    if not (mine_splinters(2, mine) > mine_splinters(1, mine) >= 1):
        return False, f"mining yields not increasing with tier ({mine})"

    # 4. Cargo scoop gate: needs Fuel Scoops AND hold space.
    if can_scoop(False, 0, 20):
        return False, "scooped cargo without Fuel Scoops"
    if can_scoop(True, 20, 20):
        return False, "scooped cargo into a full hold"
    if not can_scoop(True, 5, 20):
        return False, "could not scoop with scoops + free hold"

    return True, (
        f"police hunt when wanted (<{pt}=1, >={pt}=2 viper); murder "
        f"trader+{fw['trader_crime']}/police+{fw['police_crime']} -> Offender; "
        f"mining tier1={mine[2]}/tier2={mine[1]} splinters; scoop needs "
        f"Fuel Scoops + hold space; police cadence {fw['cadence']}f"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "world: " + msg)
