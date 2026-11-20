package leet.sim

import spinal.core.sim._
import leet.bbc.Adc7002
import org.scalatest.funsuite.AnyFunSuite

class BbcAdcSim extends AnyFunSuite {
  test("ADC channel selection, exact conversion time, retained results and restart") {
    SimConfig.compile(Adc7002()).doSim { dut =>
      SimTimeout(2000000)
      dut.clockDomain.forkStimulus(10)
      dut.io.tick #= false
      dut.io.read #= false
      dut.io.write #= false
      dut.io.address #= 0
      dut.io.data #= 0
      val samples = Seq(0x0000, 0x4567, 0x8000, 0xffff)
      val channels = samples.zipWithIndex.map { case (v, i) => BigInt(v) << (i * 16) }.reduce(_ | _)
      dut.io.channels #= channels
      dut.clockDomain.waitSampling(3)
      def step(tick: Boolean = false): Unit = {
        dut.io.tick #= tick
        dut.clockDomain.waitSampling()
        sleep(1)
        dut.io.tick #= false
      }
      def write(a: Int, d: Int): Unit = {
        dut.io.address #= a
        dut.io.data #= d
        dut.io.write #= true
        step()
        dut.io.write #= false
      }
      def read(a: Int): Int = {
        dut.io.address #= a
        sleep(1)
        dut.io.result.toInt
      }
      assert(
        read(0) == 0x40 && !dut.io.eocN.toBoolean,
        "reset must expose original idle status and CB1 pin"
      )
      var last = 0
      var lastControl = 0
      for (long <- Seq(false, true); channel <- 0 until 4) {
        val control = channel | 4 | (if (long) 8 else 0)
        write(0, control)
        assert(dut.io.eocN.toBoolean && (read(0) & 0xcf) == (0x80 | lastControl))
        assert((read(1) << 8 | read(2)) == last, "starting a conversion must retain completed data")
        for (_ <- 0 until 3) step()
        assert(dut.io.eocN.toBoolean, "conversion must only count 1 MHz enables")
        val duration = if (long) 10000 else 4000
        for (_ <- 0 until duration - 1) step(true)
        assert(dut.io.eocN.toBoolean)
        step(true)
        assert(!dut.io.eocN.toBoolean)
        last = samples(channel) & 0xfff0
        assert((read(1) << 8 | read(2)) == last)
        assert(
          read(0) == (0x40 | ((last >> 14) << 4) | control),
          "status must preserve channel/flag/mode and expose result MSBs"
        )
        assert(read(3) == (last & 0xff), "both low-byte addresses must alias")
        lastControl = control
        dut.io.address #= 1
        dut.io.read #= true
        step()
        dut.io.read #= false
        assert(dut.io.eocN.toBoolean, "accepted high-byte read must acknowledge EOC")
        assert((read(1) << 8 | read(2)) == last, "acknowledgement retains the result")
      }
      write(0, 8)
      for (_ <- 0 until 9999) step(true)
      write(0, 1)
      step(true)

      assert(dut.io.eocN.toBoolean, "new start must cancel the old completion")
      write(1, 255)
      write(2, 255)
      write(3, 255)
      for (_ <- 0 until 3998) step(true)
      assert(dut.io.eocN.toBoolean)
      step(true)
      assert(!dut.io.eocN.toBoolean)
      assert((read(1) << 8 | read(2)) == 0x4560)
    }
  }
}
