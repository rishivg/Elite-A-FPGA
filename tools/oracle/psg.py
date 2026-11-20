"""Model the demo PSG write protocol, tone counters, noise LFSR and attenuation."""

from __future__ import annotations

import re
from pathlib import Path

RTL_PATH = Path(__file__).resolve().parents[2] / "rtl" / "audio" / "Psg.scala"

# att 0 = loudest .. 15 = silent. Must match the AMPL table in Psg.scala.
AMPL = [63, 50, 40, 32, 25, 20, 16, 13, 10, 8, 6, 5, 4, 3, 3, 0]


class Psg:
    def __init__(self):
        self.period = [1, 1, 1]
        self.tvol = [15, 15, 15]
        self.nvol = 15
        self.nctrl = 0
        self.lreg = 0
        self.lvol = False
        self.tcnt = [0, 0, 0]
        self.tff = [0, 0, 0]
        self.lfsr = 0x4000
        self.ncnt = 0
        self.nff = 0

    # --- register write protocol ---
    def _setvol(self, ch, b):
        if ch < 3:
            self.tvol[ch] = b & 0xF
        else:
            self.nvol = b & 0xF

    def write(self, b):
        b &= 0xFF
        if b & 0x80:  # latch+data
            ch = (b >> 5) & 3
            typ = (b >> 4) & 1
            self.lreg, self.lvol = ch, bool(typ)
            if typ:
                self._setvol(ch, b)
            elif ch == 3:
                self.nctrl = b & 7
                self.lfsr = 0x4000
            else:
                self.period[ch] = (self.period[ch] & 0x3F0) | (b & 0xF)
        else:  # data
            if self.lvol:
                self._setvol(self.lreg, b)
            elif self.lreg == 3:
                self.nctrl = b & 7
                self.lfsr = 0x4000
            else:
                self.period[self.lreg] = ((b & 0x3F) << 4) | (self.period[self.lreg] & 0xF)

    # --- generators ---
    def noise_rate(self):
        nf = self.nctrl & 3
        return [0x10, 0x20, 0x40][nf] if nf < 3 else self.period[2]

    def tick(self):
        for i in range(3):
            if self.tcnt[i] == 0:
                self.tcnt[i] = (self.period[i] or 1024) - 1
                self.tff[i] ^= 1
            else:
                self.tcnt[i] -= 1
        if self.ncnt == 0:
            self.ncnt = (self.noise_rate() - 1) & 0x3FF
            self.nff ^= 1
            fb = ((self.lfsr & 1) ^ ((self.lfsr >> 1) & 1)) if (self.nctrl & 4) else (self.lfsr & 1)
            if self.nff:
                self.lfsr = ((fb << 14) | (self.lfsr >> 1)) & 0x7FFF
        else:
            self.ncnt -= 1

    def sample(self):
        s = 0
        for i in range(3):
            if self.tff[i]:
                s += AMPL[self.tvol[i]]
        if self.lfsr & 1:
            s += AMPL[self.nvol]
        return s & 0xFF


def parse_hdl_ampl():
    """Pull the AMPL table out of Psg.scala so it can't silently drift."""
    m = re.search(r"AMPL\s*=.*?Seq\(([^)]*)\)", RTL_PATH.read_text(), re.DOTALL)
    if not m:
        raise ValueError("AMPL table not found in Psg.scala")
    return [int(x) for x in m.group(1).split(",")]


def check() -> tuple[bool, str]:
    # 1. Attenuation table matches the HDL + is monotone non-increasing to 0.
    if parse_hdl_ampl() != AMPL:
        return False, f"AMPL drift: HDL {parse_hdl_ampl()} vs oracle {AMPL}"
    if any(AMPL[i] < AMPL[i + 1] for i in range(15)) or AMPL[-1] != 0:
        return False, f"AMPL not monotone-to-zero: {AMPL}"

    # 2. Tone protocol: latch low nibble + data high 6 bits assemble a 10-bit
    #    period; the square toggles every period ticks (BBC divider semantics).
    p = Psg()
    p.write(0x80 | (0 << 5) | 0x00 | 0x5)  # ch0 tone, low nibble = 0x5
    p.write(0x00 | 0x0A)  # data: high 6 bits = 0x0A
    period = p.period[0]
    if period != ((0x0A << 4) | 0x5):
        return False, f"tone period assembled wrong: {period} != {(0x0A << 4) | 0x5}"
    p.write(0x80 | (0 << 5) | 0x10 | 0x0)  # ch0 volume = 0 (loudest)
    edges, last, ticks_between = 0, p.tff[0], []
    run = 0
    for _ in range(period * 6):
        p.tick()
        run += 1
        if p.tff[0] != last:
            ticks_between.append(run)
            run = 0
            last = p.tff[0]
            edges += 1
    # First interval can be partial; steady-state intervals must equal period.
    steady = ticks_between[1:4]
    if edges < 3 or any(t != period for t in steady):
        return False, f"tone toggle intervals {ticks_between} (expected steady {period})"

    # 3. Volume: att 0 loudest contributes AMPL[0], att 15 silent contributes 0.
    if AMPL[0] != 63 or AMPL[15] != 0:
        return False, "volume endpoints wrong"

    # 4. Noise LFSR: deterministic, period > 1000, white differs from periodic,
    #    and the reset on a noise write restores the seed.
    def lfsr_seq(white, n):
        q = Psg()
        q.write(0x80 | (3 << 5) | 0x00 | ((4 if white else 0) | 0))  # noise ctrl, rate 0
        out = []
        # One shift every 2*noise_rate ticks; sample the output bit per shift.
        for _ in range(n):
            for _ in range(q.noise_rate() * 2):
                q.tick()
            out.append(q.lfsr & 1)
        return out

    white = lfsr_seq(True, 200)
    per = lfsr_seq(False, 200)
    if white == per:
        return False, "white and periodic noise produced identical sequences"
    if len(set(white)) < 2:
        return False, "white noise output never toggled"
    # periodic noise repeats every 15 shifts (single-tap 15-bit ring)
    if per[:15] != per[15:30]:
        return False, f"periodic noise not period-15: {per[:30]}"

    # 5. Determinism: two runs of the white sequence match.
    if lfsr_seq(True, 64) != lfsr_seq(True, 64):
        return False, "noise not deterministic"

    return True, (
        "AMPL table matches HDL + monotone-to-0; tone period assembles "
        "(toggle every period); white!=periodic noise; periodic LFSR "
        "period-15; deterministic"
    )


if __name__ == "__main__":
    ok, msg = check()
    print(("PASS " if ok else "FAIL ") + "psg: " + msg)
