"""Model the firmware LCG used for star and particle respawn."""

from __future__ import annotations


class FwRng:
    """Mirrors fw/main.c:
    static unsigned rng = 0xACE1u;
    rng = rng * 1103515245u + 12345u;
    v = (rng >> 16) & 0xFFFF
    return (v % (2*span + 1)) - span
    """

    MUL = 1103515245
    ADD = 12345
    MASK32 = 0xFFFFFFFF

    def __init__(self, seed: int = 0xACE1):
        self.state = seed & self.MASK32

    def next_word(self) -> int:
        self.state = (self.state * self.MUL + self.ADD) & self.MASK32
        return (self.state >> 16) & 0xFFFF

    def signed(self, span: int) -> int:
        v = self.next_word()
        return (v % (2 * span + 1)) - span


def check() -> tuple[bool, str]:
    """Self-consistency: known-seed evolution lands at a stable, recorded sequence."""
    r = FwRng()
    seen = [r.next_word() for _ in range(4)]
    # These are derived purely from the LCG arithmetic above; if you change
    # the multiplier/adder/seed in firmware, regenerate by running this file
    # and pasting the new first 8 words here.
    expected = [0x0BFC, 0x2484, 0x1050, 0x677A]
    if seen != expected:
        return (
            False,
            f"LCG drifted — first 4 words {[hex(x) for x in seen]}, expected {[hex(x) for x in expected]}",
        )
    return True, f"LCG first 4 words match recorded sequence ({[hex(x) for x in expected]})"


if __name__ == "__main__":
    # Helper for regenerating the expected[] list after a firmware-side change.
    r = FwRng()
    print("first 8 words:", [hex(r.next_word()) for _ in range(8)])
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "rng: " + msg)
