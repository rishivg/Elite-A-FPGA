"""Shared demo constants and Q8.8 helpers; source-table checks live in each model."""

from __future__ import annotations

FRAC = 8
ONE = 1 << FRAC


def to_fixed(x: float) -> int:
    """Return a signed Q8.8 integer; wrap to 16 bits only for MMIO writes."""
    return int(round(x * ONE))


def to_float(q: int) -> float:
    """Q8.8 signed int -> float."""
    return q / ONE


def fxmul(a: int, b: int) -> int:
    """Matches firmware FXMUL(a,b) = ((a*b) >> FRAC) on signed ints.

    Python's >> on negatives is an arithmetic shift, so this is identical to
    the C right-shift on signed ints for inputs in our range.
    """
    return (a * b) >> FRAC


def tdiv(a: int, b: int) -> int:
    """Divide signed integers with C truncation toward zero."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def clampi(x: int, lo: int, hi: int) -> int:
    return lo if x < lo else hi if x > hi else x


def signum(x: int) -> int:
    return (x > 0) - (x < 0)


# Render layout (rtl/common/Config.scala default Soc rcfg)
SCREEN_W = 128
SCREEN_H = 128
VIEW_H = 96  # top region = space view; bottom (SCREEN_H - VIEW_H) = dashboard
FOCAL = 160
CX = SCREEN_W // 2
CY = VIEW_H // 2

# Firmware constants (fw/main.c)
NSHIPS_MAX = 12  # Elite NOSH
NSTARS = 18  # Elite NOST (normal space)
ROLL_MAX = 1
PITCH_MAX = 1
SPEED_MIN = 1
SPEED_MAX = 12
SPEED_STEP = 16  # firmware: forward step (Q8.8) = delta * SPEED_STEP

# Combat
LASER_HEAT_MAX = 32
SHIP_HITS_INIT = 3
NPARTS_MAX = 32
BOOM_PARTICLES = 14
PART_LIFETIME = 16
PART_SPEED = 80  # Q8.8 per-axis velocity bound
CROSS_HALF = 5  # crosshair half-extent in pixels
ENERGY_MAX = 15
ENERGY_REGEN_MASK = 3  # firmware regens energy when (frame_no & MASK) == 0 AND idle

# Tactics
SHIELDS_MAX = 15
DYING_FRAMES = 30
HIT_FLASH_FRAMES = 2
FIRE_COOLDOWN = 14
AGGRESSION_MASK = 0x3F  # 6-bit LCG draw vs aggression byte
ENEMY_FIRE_RANGE = 40  # view units; squared in firmware: ENEMY_FIRE_RANGE_Q2
ENEMY_CLOSE_RANGE = 20  # view units
SHIP_HITS_HOSTILE = 4  # current scene_init seeds hostiles with 4 hits

# Ship behaviour enum (mirrors fw/main.c)
BEHAVIOR_STATIC = 0
BEHAVIOR_HOSTILE = 1

# Ship type/state enum
SHIP_EMPTY = 0
SHIP_ALIVE = 1

# Dashboard + scanner
SCAN_CX = 104
SCAN_CY = 112
SCAN_HW = 22
SCAN_HH = 9
SCAN_RANGE = 60  # view units (will be Q8.8-multiplied in scanner.py)

# Hyperspace
FUEL_MAX = 70
HYPERSPACE_COST = 7
HYPER_FLASH_FRAMES = 12
CHART_X_SCALE = 2
CHART_Y_SCALE = 3


# Player MMIO input packing:
#   byte0 = roll (signed int8)   byte1 = pitch (signed int8)
#   byte2 = speed (signed int8)  byte3 = button bits (bit0 = fire)
def pack_input(roll: int = 0, pitch: int = 0, speed: int = 0, fire: bool = False) -> int:
    """Build the 32-bit word the SoC presents to the firmware over MMIO[INPUT]."""
    btn = 1 if fire else 0
    return ((btn & 0xFF) << 24) | ((speed & 0xFF) << 16) | ((pitch & 0xFF) << 8) | (roll & 0xFF)
