"""Model the demo Constrictor mission spawn, kill and reward rules."""

from __future__ import annotations

import re
from pathlib import Path

FW_PATH = Path(__file__).resolve().parents[2] / "fw" / "main.c"

# State enum (must match the firmware order).
NONE, ACTIVE, DONE = 0, 1, 2


def parse_fw():
    text = FW_PATH.read_text()

    def dint(pat, default=None):
        m = re.search(pat, text)
        return int(m.group(1)) if m else default

    bounty = dint(r"#define\s+MISSION_BOUNTY\s+(\d+)")
    target = dint(r"static int mission_target_sys\s*=\s*(\d+)")
    start_sym = re.search(r"static int mission_state\s*=\s*MISSION_(\w+)", text)
    start_state = (
        {"NONE": NONE, "ACTIVE": ACTIVE, "DONE": DONE}[start_sym.group(1)] if start_sym else None
    )
    # Constrictor hits + that it is HOSTILE, from its scene_init spawn line.
    cm = re.search(r"nwshp\([^;]*BEHAVIOR_HOSTILE,\s*\d+,\s*(\d+)\);\s*\n\s*if \(cc >= 0\)", text)
    constr_hits = int(cm.group(1)) if cm else None
    # A regular hostile fighter's hits, for the "tougher" comparison.
    fm = re.search(r"BEHAVIOR_HOSTILE,\s*40,\s*(\d+)\);", text)
    fighter_hits = int(fm.group(1)) if fm else None
    return {
        "bounty": bounty,
        "target": target,
        "start": start_state,
        "constr_hits": constr_hits,
        "fighter_hits": fighter_hits,
    }


def mission_next(state: int) -> int:
    """Port of fw mission_next: only ACTIVE advances (to DONE)."""
    return DONE if state == ACTIVE else state


def constrictor_spawns(state: int, current_sys: int, target_sys: int) -> bool:
    return state == ACTIVE and current_sys == target_sys


def kill_constrictor(state: int, credits: int, bounty: int):
    """Returns (new_state, new_credits, committed_crime)."""
    if state != ACTIVE:
        return state, credits, False  # already done — no double payout
    return mission_next(state), credits + bounty, False  # legal kill, pays out


def check() -> tuple[bool, str]:
    fw = parse_fw()
    for k in ("bounty", "target", "start", "constr_hits", "fighter_hits"):
        if fw[k] is None:
            return False, f"could not parse {k} from firmware"

    # 1. State machine (the boundaries mission_self_test checks on the CPU).
    if mission_next(ACTIVE) != DONE:
        return False, "ACTIVE did not advance to DONE"
    if mission_next(DONE) != DONE or mission_next(NONE) != NONE:
        return False, "DONE/NONE are not terminal"
    if fw["bounty"] <= 0:
        return False, f"bounty {fw['bounty']} is not positive"

    # 2. The hunt starts active, and the Constrictor only appears in its target
    #    system while the hunt is on.
    if fw["start"] != ACTIVE:
        return False, "the mission does not start ACTIVE"
    t = fw["target"]
    if not constrictor_spawns(ACTIVE, t, t):
        return False, "Constrictor missing from its target system while ACTIVE"
    if constrictor_spawns(ACTIVE, t + 1, t):
        return False, "Constrictor spawned in the wrong system"
    if constrictor_spawns(DONE, t, t):
        return False, "Constrictor reappeared after the mission was done"

    # 3. Killing it pays the bounty once and closes the mission; it's a legal
    #    kill (no crime), and a second 'kill' pays nothing.
    st, cr, crime = kill_constrictor(ACTIVE, 1000, fw["bounty"])
    if st != DONE or cr != 1000 + fw["bounty"] or crime:
        return False, f"first kill wrong: state {st}, credits {cr}, crime {crime}"
    st2, cr2, _ = kill_constrictor(st, cr, fw["bounty"])
    if st2 != DONE or cr2 != cr:
        return False, "a second kill paid out again"

    # 4. The prototype is tougher than an ordinary fighter.
    if not (fw["constr_hits"] > fw["fighter_hits"]):
        return (
            False,
            f"Constrictor ({fw['constr_hits']}) not tougher than a fighter ({fw['fighter_hits']})",
        )

    return True, (
        f"starts ACTIVE; Constrictor (hits {fw['constr_hits']} > fighter "
        f"{fw['fighter_hits']}) only in system {t} while hunting; kill pays "
        f"{fw['bounty']} tenths once + closes the mission (legal); "
        f"DONE/NONE terminal"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "mission: " + msg)
