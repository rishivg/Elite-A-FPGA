package leet.audio

import spinal.core._

/** SN76489-style programmable sound generator.
  *
  * Three square-wave tone channels + one LFSR noise channel, each with 4-bit attenuation, mixed to
  * an unsigned `sample` (for a small DAC / inspection) plus a first-order sigma-delta `pdm` 1-bit
  * output for a passive RC filter on a board. Driven by the classic single-byte latch/data
  * protocol, so firmware writes the same stream a real SN76489 driver would.
  *
  * Write protocol (one byte per `write` pulse): 1 cc t dddd latch+data : cc=channel(0..3), t=type(0
  * tone/noise,1 volume), dddd = low 4 data bits (tone) / 4-bit attenuation (volume) / low 3 = noise
  * control (ch 3, type 0) 0 x dddddd data : high 6 bits of the latched tone period
  *
  * Internal "chip clock" = core clock / `prescale`. A tone channel toggles its square output every
  * `period` chip-ticks (full square period = 2*period* prescale core cycles). The 15-bit noise LFSR
  * shifts at one of three rates or tracks tone-2's period. tools/oracle/psg.py mirrors all of this
  * bit-exact.
  */
case class Psg(
    prescale: Int = 16,
    coreClockHz: Int = 0,
    initialTonePeriod: Int = 1,
    halfFirstTick: Boolean = false,
    bbcDigitalProfile: Boolean = false
) extends Component {
  require(prescale >= 2)
  require(coreClockHz == 0 || coreClockHz >= 250000)
  require(initialTonePeriod >= 0 && initialTonePeriod <= 1023)
  require(!halfFirstTick || coreClockHz > 0)
  require(!halfFirstTick || coreClockHz % 500000 == 0)
  require(!bbcDigitalProfile || (halfFirstTick && coreClockHz % 500000 == 0))
  val io = new Bundle {
    val write = in Bool ()
    val data = in Bits (8 bits)
    val sample = out UInt (8 bits) // mixed level (0..~252)
    val pdm = out Bool () // 1-bit sigma-delta audio out
    val tone = out Bits (3 bits) // raw square outputs (for the testbench)
    val noise = out Bool ()
  }

  // 16-entry attenuation -> amplitude table (att 0 = loudest, 15 = silent),
  // ~2 dB/step. Per-channel max 63 so a 4-channel sum stays in 8 bits.
  val AMPL = Vec(
    (0 until 16).map(i =>
      U(Seq(63, 50, 40, 32, 25, 20, 16, 13, 10, 8, 6, 5, 4, 3, 3, 0)(i), 8 bits)
    )
  )

  // --- register file ---
  val tonePeriod = Vec(Reg(UInt(10 bits)) init (initialTonePeriod), 3)
  val generatorPeriods = if (bbcDigitalProfile) {
    val periods = Vec(UInt(10 bits), 3)
    for (i <- 0 until 3) { periods(i) := tonePeriod(i) }
    periods
  } else { tonePeriod }
  val toneVol = Vec(Reg(UInt(4 bits)) init (15), 3) // start silent
  val noiseVol = Reg(UInt(4 bits)) init (15)
  val noiseCtrl = Reg(UInt(3 bits)) init (0) // {fb, nf[1:0]}
  val generatorNoiseCtrl = if (bbcDigitalProfile) {
    val control = UInt(3 bits)
    control := noiseCtrl
    control
  } else { noiseCtrl }

  val latchedReg = Reg(UInt(2 bits)) init (0) // channel 0..3
  val latchedVol = Reg(Bool()) init (False) // false=tone/noise, true=vol
  val noiseReset = False

  // --- write decode ---
  def setToneLo(ch: UInt, d: Bits): Unit =
    when(ch < 3) {
      tonePeriod(ch.resized) := (tonePeriod(ch.resized)(9 downto 4) ## d(3 downto 0)).asUInt
      if (bbcDigitalProfile) {
        generatorPeriods(ch.resized) := (tonePeriod(ch.resized)(9 downto 4) ## d(3 downto 0)).asUInt
      }
    }
  def setToneHi(ch: UInt, d: Bits): Unit =
    when(ch < 3) {
      tonePeriod(ch.resized) := (d(5 downto 0) ## tonePeriod(ch.resized)(3 downto 0)).asUInt
      if (bbcDigitalProfile) {
        generatorPeriods(ch.resized) := (d(5 downto 0) ## tonePeriod(ch.resized)(3 downto 0)).asUInt
      }
    }
  def setVol(ch: UInt, d: Bits): Unit = {
    when(ch === 0) { toneVol(0) := d(3 downto 0).asUInt }
    when(ch === 1) { toneVol(1) := d(3 downto 0).asUInt }
    when(ch === 2) { toneVol(2) := d(3 downto 0).asUInt }
    when(ch === 3) { noiseVol := d(3 downto 0).asUInt }
  }

  when(io.write) {
    when(io.data(7)) { // latch+data byte
      val ch = io.data(6 downto 5).asUInt
      val typ = io.data(4)
      latchedReg := ch
      latchedVol := typ
      when(typ) {
        setVol(ch, io.data)
      } otherwise {
        when(ch === 3) {
          noiseCtrl := io.data(2 downto 0).asUInt
          if (bbcDigitalProfile) { generatorNoiseCtrl := io.data(2 downto 0).asUInt }
          noiseReset := True
        }
          .otherwise { setToneLo(ch, io.data) }
      }
    } otherwise { // data byte -> high 6 bits / vol
      when(latchedVol) {
        setVol(latchedReg, io.data)
      } otherwise {
        when(latchedReg === 3) {
          noiseCtrl := io.data(2 downto 0).asUInt
          if (bbcDigitalProfile) { generatorNoiseCtrl := io.data(2 downto 0).asUInt }
          noiseReset := True
        }
          .otherwise { setToneHi(latchedReg, io.data) }
      }
    }
  }

  // --- chip-clock prescaler ---
  val integerDivider = if (coreClockHz == 0) prescale else coreClockHz / 250000
  val noiseTick = Bool()
  val tick = if (coreClockHz == 0 || coreClockHz % 250000 == 0) {
    // Exact ratios need only a small counter (7 bits at 25 MHz), not a
    // wide fractional accumulator. Divider one produces a tick every cycle.
    val preCnt =
      Reg(UInt((log2Up(integerDivider) max 1) bits)) init (if (halfFirstTick) integerDivider / 2
                                                           else 0)
    val pulse = preCnt === (integerDivider - 1)
    if (bbcDigitalProfile) {
      noiseTick := pulse || preCnt === (integerDivider / 2 - 1)
    } else { noiseTick := pulse }
    preCnt := Mux(pulse, U(0), preCnt + 1)
    pulse
  } else {
    // BBC PSG tone half-period ticks are 250 kHz. Keep that frequency
    // independent of the board/CPU clock, including fractional ratios.
    val phase = Reg(UInt(log2Up(coreClockHz + 250000) bits)) init (0)
    val pulse = phase >= coreClockHz - 250000
    noiseTick := pulse
    phase := Mux(pulse, phase + 250000 - coreClockHz, phase + 250000)
    pulse
  }

  // --- tone generators ---
  val toneCnt = Vec(Reg(UInt(10 bits)) init (0), 3)
  val toneFf = Vec(Reg(Bool()) init (False), 3)
  for (i <- 0 until 3) {
    when(tick) {
      when(toneCnt(i) === 0) {
        // Count this tick as part of the interval. Zero represents 1024 on
        // the BBC SN76489, which naturally wraps to 1023 in this register.
        // The BBC digital profile applies period writes before the sampled
        // generator step, including a write coincident with this tick.
        toneCnt(i) := (generatorPeriods(i) - 1).resized
        toneFf(i) := !toneFf(i)
      } otherwise {
        toneCnt(i) := toneCnt(i) - 1
      }
    }
    io.tone(i) := toneFf(i)
  }

  // --- noise generator (15-bit LFSR) ---
  val lfsr = Reg(UInt(15 bits)) init (0x4000)
  val noiseCnt = Reg(UInt((if (bbcDigitalProfile) 11 else 10) bits)) init (0)
  val noiseFf = RegInit(False)
  // shift rate: 00->0x10, 01->0x20, 10->0x40, 11-> track tone 2's period
  val nf = generatorNoiseCtrl(1 downto 0)
  val fbWhite = generatorNoiseCtrl(2)
  val noiseRate = nf.mux(
    0 -> U(0x10, 10 bits),
    1 -> U(0x20, 10 bits),
    2 -> U(0x40, 10 bits),
    default -> generatorPeriods(2)
  )
  // The BBC reference decrements noise counters at 500 kHz. A linked zero
  // toggles each step, while tone zero still means 1024. Keep half-tick units
  // so entering or leaving zero retains the preceding counter's phase.
  val noiseReload = if (bbcDigitalProfile) {
    Mux(noiseRate === 0, U(1, 11 bits), noiseRate.resize(11) |<< 1)
  } else { noiseRate }
  // white noise feedback = parity(bit0 ^ bit1); periodic = bit0
  val shiftState = if (bbcDigitalProfile) Mux(noiseReset, U(0x4000, 15 bits), lfsr) else lfsr
  val fb = Mux(fbWhite, shiftState(0) ^ shiftState(1), shiftState(0))
  // In the BBC digital profile, a control write establishes its seed before
  // a coincident sampled shift. The default synchronous profile retains its
  // earlier reset priority. Neither profile resets the running noise counter.
  if (bbcDigitalProfile) { when(noiseReset) { lfsr := 0x4000 } }
  when(noiseTick) {
    when(noiseCnt === 0) {
      noiseCnt := (noiseReload - 1).resized
      noiseFf := !noiseFf
      when(!noiseFf) { lfsr := (fb ## shiftState(14 downto 1)).asUInt }
    } otherwise {
      noiseCnt := noiseCnt - 1
    }
  }
  // Control writes reset only the shift register. Both half-periods keep
  // running, so rate changes take effect at their normal reload boundary.
  if (!bbcDigitalProfile) { when(noiseReset) { lfsr := 0x4000 } }
  io.noise := lfsr(0)

  // --- mix ---
  val mix = (0 until 3)
    .map(i => Mux(toneFf(i), AMPL(toneVol(i)), U(0, 8 bits)).resize(10 bits))
    .reduce(_ + _) + Mux(lfsr(0), AMPL(noiseVol), U(0, 8 bits)).resize(10 bits)
  io.sample := mix.resize(8 bits)

  // --- first-order sigma-delta to 1 bit ---
  val acc = Reg(UInt(9 bits)) init (0)
  acc := (acc(7 downto 0) +^ io.sample)
  io.pdm := acc(8)
}
