"""Model demo jump requests, fuel consumption, tunnel frames and scene reseeding."""

from __future__ import annotations

from . import spec


class HyperspaceState:
    def __init__(self) -> None:
        self.fuel = spec.FUEL_MAX
        self.current_sys = 0
        self.target_sys = 0
        self.hyper_flash = 0
        self.chart_mode = 0

    def jump(self) -> bool:
        """Attempt a hyperspace jump. Returns True iff the jump fires."""
        if self.fuel < spec.HYPERSPACE_COST:
            return False
        if self.target_sys == self.current_sys:
            return False
        self.fuel -= spec.HYPERSPACE_COST
        self.current_sys = self.target_sys
        self.hyper_flash = spec.HYPER_FLASH_FRAMES
        self.chart_mode = 0
        return True

    def tick(self) -> bool:
        """One frame of witchspace_tick. Returns True iff the scene-reseed
        signal should fire this frame (i.e., hyper_flash just hit 0)."""
        if self.hyper_flash <= 0:
            return False
        self.hyper_flash -= 1
        return self.hyper_flash == 0


def check() -> tuple[bool, str]:
    # 1. Jump rejects when fuel too low.
    h = HyperspaceState()
    h.fuel = spec.HYPERSPACE_COST - 1
    h.target_sys = 5
    if h.jump():
        return False, "jump fired with fuel < HYPERSPACE_COST"
    if h.fuel != spec.HYPERSPACE_COST - 1:
        return False, "low-fuel rejected jump touched fuel"

    # 2. Jump rejects when target == current.
    h = HyperspaceState()
    h.target_sys = h.current_sys = 42
    if h.jump():
        return False, "jump fired with target == current"
    if h.fuel != spec.FUEL_MAX:
        return False, "rejected jump touched fuel"

    # 3. Successful jump: fuel decremented, current_sys updated, hyper_flash armed.
    h = HyperspaceState()
    h.target_sys = 99
    h.chart_mode = 1
    if not h.jump():
        return False, "valid jump rejected"
    if h.fuel != spec.FUEL_MAX - spec.HYPERSPACE_COST:
        return False, f"fuel wrong after jump: {h.fuel}"
    if h.current_sys != 99:
        return False, f"current_sys wrong after jump: {h.current_sys}"
    if h.hyper_flash != spec.HYPER_FLASH_FRAMES:
        return False, f"hyper_flash not armed: {h.hyper_flash}"
    if h.chart_mode != 0:
        return False, f"chart_mode not cleared: {h.chart_mode}"

    # 4. tick() decrements hyper_flash and signals exactly once at hit-0.
    h = HyperspaceState()
    h.target_sys = 10
    h.jump()
    reseed_count = 0
    for _ in range(spec.HYPER_FLASH_FRAMES):
        if h.tick():
            reseed_count += 1
    if reseed_count != 1:
        return False, f"re-seed fired {reseed_count} times in HYPER_FLASH_FRAMES, expected 1"
    if h.hyper_flash != 0:
        return False, f"hyper_flash not 0 after countdown: {h.hyper_flash}"

    # 5. Extra ticks after countdown don't re-fire.
    for _ in range(5):
        if h.tick():
            return False, "tick fired re-seed after countdown completed"

    # 6. Sequence: 10 successful jumps drain fuel by 10*COST.
    h = HyperspaceState()
    for i in range(10):
        h.target_sys = i + 1
        h.jump()
        for _ in range(spec.HYPER_FLASH_FRAMES):
            h.tick()
    if h.fuel != spec.FUEL_MAX - 10 * spec.HYPERSPACE_COST:
        return False, f"fuel after 10 jumps: {h.fuel}"

    # 7. 11th jump fails because fuel = 0 < COST.
    h.target_sys = 100
    if h.jump():
        return False, "11th jump (fuel=0) fired"

    return True, (
        "low-fuel + equal-target rejects; successful jump arms hyper_flash + "
        "clears chart_mode + decrements fuel; re-seed fires exactly once per jump; "
        "10 sequential jumps drain fuel correctly; 11th rejected on empty fuel"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "hyperspace: " + msg)
