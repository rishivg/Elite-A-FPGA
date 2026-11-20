package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.audio.Psg
import org.scalatest.funsuite.AnyFunSuite
import scala.collection.mutable.ArrayBuffer

/** Drives the SN76489-style PSG over its latch/data byte protocol and checks the hardware
  * behaviour: tone-divider period, the attenuation table, and that the noise channel actually
  * toggles. The exact LFSR sequence + protocol algebra are locked bit-for-bit by
  * tools/oracle/psg.py.
  *
  * Run with `sbt "testOnly leet.sim.PsgSim"`.
  */
class PsgSim extends AnyFunSuite {
  val PRESCALE = 4
  // AMPL table from Psg.scala (att -> amplitude).
  val AMPL = Array(63, 50, 40, 32, 25, 20, 16, 13, 10, 8, 6, 5, 4, 3, 3, 0)

  test("BBC tone frequency is independent of integer or fractional board clocks") {
    for (clockHz <- Seq(250000, 25000000, 25175000, 100000000)) {
      SimConfig.compile(Psg(coreClockHz = clockHz)).doSim { dut =>
        dut.clockDomain.forkStimulus(10)
        dut.io.write #= false
        dut.io.data #= 0
        dut.clockDomain.waitSampling(3)
        def write(value: Int): Unit = {
          dut.io.write #= true
          dut.io.data #= value
          dut.clockDomain.waitSampling()

          dut.io.write #= false
        }
        write(0x86)
        write(0)
        write(0x90)
        val expected = clockHz.toDouble * 6 / 250000
        dut.clockDomain.waitSampling(expected.toInt * 2)
        val edges = ArrayBuffer[Int]()
        var last = dut.io.tone.toInt & 1
        for (cycle <- 0 until (expected * 22).toInt) {
          dut.clockDomain.waitSampling()
          val bit = dut.io.tone.toInt & 1
          if (bit != last) {
            edges += cycle
            last = bit
          }
        }
        val intervals = edges.sliding(2).map(x => x(1) - x(0)).toSeq
        assert(intervals.size >= 18)
        assert(intervals.forall(x => x >= math.floor(expected) && x <= math.ceil(expected)))
        assert(math.abs(intervals.sum.toDouble / intervals.size - expected) < 0.1)
      }
    }
  }

  test("tone period, attenuation table, and noise activity") {
    SimConfig.compile(Psg(prescale = PRESCALE)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      dut.io.write #= false
      dut.io.data #= 0
      dut.clockDomain.waitSampling(3)

      def psgWrite(b: Int): Unit = {
        dut.io.write #= true
        dut.io.data #= (b & 0xff)
        dut.clockDomain.waitSampling()
        dut.io.write #= false
      }

      // --- Tone channel 0: period P, full volume; others silent (init att=15) ---
      val P = 6
      psgWrite(0x80 | (0 << 5) | 0x00 | (P & 0xf)) // ch0 tone low nibble
      psgWrite(0x00 | ((P >> 4) & 0x3f)) // data: high 6 bits
      psgWrite(0x80 | (0 << 5) | 0x10 | 0x0) // ch0 volume = 0 (loudest)
      dut.clockDomain.waitSampling(4)

      // Record core-cycle indices where tone(0) flips + the sample levels seen.
      val edges = ArrayBuffer[Int]()
      var last = (dut.io.tone.toInt & 1)
      var sMin = 999
      var sMax = -1
      for (c <- 0 until 20 * (P + 1) * PRESCALE) {
        dut.clockDomain.waitSampling()
        val t = dut.io.tone.toInt & 1
        if (t != last) {
          edges += c
          last = t
        }
        val s = dut.io.sample.toInt
        if (s < sMin) sMin = s
        if (s > sMax) sMax = s
      }
      assert(edges.length >= 4, s"tone(0) barely toggled (${edges.length} edges)")
      // BBC SN76489 steady-state half-period = P*PRESCALE core cycles.
      val intervals = edges.sliding(2).map(p => p(1) - p(0)).toList
      val expected = P * PRESCALE
      val steady = intervals.drop(1).take(4)
      assert(
        steady.forall(_ == expected),
        s"tone half-period intervals $steady, expected $expected"
      )
      // Amplitude: only ch0 sounds, so the sample is 0 (low) or AMPL(0)=63 (high).
      assert(
        sMax == AMPL(0) && sMin == 0,
        s"tone sample range [$sMin,$sMax], expected [0,${AMPL(0)}]"
      )
      println(s"tone: half-period=$expected cyc, sample in {0,${AMPL(0)}}")

      // --- Volume change: att=3 -> AMPL(3)=32 peak ---
      psgWrite(0x80 | (0 << 5) | 0x10 | 0x3) // ch0 volume = 3
      dut.clockDomain.waitSampling(4)
      var vMax = -1
      for (_ <- 0 until 8 * (P + 1) * PRESCALE) {
        dut.clockDomain.waitSampling()
        vMax = math.max(vMax, dut.io.sample.toInt)
      }
      assert(vMax == AMPL(3), s"att=3 peak sample $vMax, expected ${AMPL(3)}")
      println(s"volume: att=3 -> peak ${AMPL(3)}")

      // --- Noise: silence the tone, enable white noise at full volume ---
      psgWrite(0x80 | (0 << 5) | 0x10 | 0xf) // ch0 volume = 15 (silent)
      psgWrite(0x80 | (3 << 5) | 0x00 | (0x4 | 0x0)) // noise: white, rate 0
      psgWrite(0x80 | (3 << 5) | 0x10 | 0x0) // noise volume = 0
      dut.clockDomain.waitSampling(4)
      var zeros = 0
      var ones = 0
      var nMax = -1
      for (_ <- 0 until 4000) {
        dut.clockDomain.waitSampling()
        if ((dut.io.noise.toBoolean)) ones += 1 else zeros += 1
        nMax = math.max(nMax, dut.io.sample.toInt)
      }
      assert(zeros > 0 && ones > 0, s"noise output never toggled (z=$zeros o=$ones)")
      assert(nMax == AMPL(0), s"noise peak sample $nMax, expected ${AMPL(0)}")
      println(s"noise: toggled (0s=$zeros 1s=$ones), peak ${AMPL(0)}")
    }
  }
}
