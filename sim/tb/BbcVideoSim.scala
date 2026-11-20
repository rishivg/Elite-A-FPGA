package leet.sim

import spinal.core.sim._
import leet.bbc.BbcVideo
import org.scalatest.funsuite.AnyFunSuite

class BbcVideoSim extends AnyFunSuite {
  test("ULA emits every byte in every bitmap pixel rate with programmable colours") {
    SimConfig.compile(BbcVideo()).doSim { dut =>
      SimTimeout(20000000)
      dut.clockDomain.forkStimulus(10)
      dut.io.lightPenPulse #= false
      dut.io.crtcWrite #= false
      dut.io.ulaWrite #= false
      dut.io.address #= false
      dut.io.data #= 0
      dut.io.scroll #= 0
      dut.io.memoryData #= 0
      dut.clockDomain.waitSampling(3)
      def step(): Unit = {
        dut.clockDomain.waitSampling()
        sleep(1)
      }
      def crtc(a: Int, d: Int): Unit = {
        dut.io.crtcWrite #= true
        dut.io.address #= false
        dut.io.data #= a
        step()
        dut.io.address #= true
        dut.io.data #= d
        step()
        dut.io.crtcWrite #= false
      }
      def ula(a: Boolean, d: Int): Unit = {
        dut.io.ulaWrite #= true
        dut.io.address #= a
        dut.io.data #= d
        step()

        dut.io.ulaWrite #= false
      }
      crtc(0, 1)
      crtc(1, 1)
      crtc(4, 126)
      crtc(6, 127)
      crtc(7, 120)
      crtc(9, 7)
      for (i <- 0 until 16) ula(true, (i << 4) | (((i ^ (i >> 1)) & 7) ^ 7))
      // The real CRTC starts with vertical display disabled. Allow its first
      // programmed field to finish before testing the bitmap shifter.
      var warmup = 0
      while (!dut.io.display.toBoolean && warmup < 80000) {
        step()
        warmup += 1
      }
      assert(warmup < 80000, "No displayed field after CRTC programming")
      for (fast <- Seq(false, true); mode <- 0 until 4; byte <- 0 until 256) {
        ula(false, (if (fast) 16 else 0) | (mode << 2))
        dut.io.memoryData #= byte
        var guard = 0
        while (
          !(dut.io.pixel.toBoolean && dut.io.display.toBoolean && dut.io.x.toInt == 0) && guard < 200
        ) {
          step()
          guard += 1
        }
        assert(guard < 200)
        for (x <- 0 until (if (fast) 8 else 16)) {
          assert(dut.io.pixel.toBoolean && dut.io.x.toInt == x)
          val shift = x >> (3 - mode)
          val shifted = ((byte << shift) | ((1 << shift) - 1)) & 255
          val index =
            ((shifted >> 7) & 1) * 8 + ((shifted >> 5) & 1) * 4 + ((shifted >> 3) & 1) * 2 + ((shifted >> 1) & 1)
          assert(
            dut.io.rgb.toInt == ((index ^ (index >> 1)) & 7),
            s"byte=$byte mode=$mode fast=$fast x=$x"
          )
          step()
          step()
        }
      }
      println("ULA: 2048 byte/rate combinations passed")
    }
  }
}
