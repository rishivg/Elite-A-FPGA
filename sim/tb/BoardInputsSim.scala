package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.top.BoardInputs
import org.scalatest.funsuite.AnyFunSuite

class BoardInputsSim extends AnyFunSuite {
  test("debounce rejects chatter, captures simultaneous keys, and opposing axes cancel") {
    SimConfig.compile(BoardInputs(debounceCycles = 6)).doSim { dut =>
      SimTimeout(10000)
      dut.clockDomain.forkStimulus(10)
      dut.io.axes #= 0
      dut.io.buttons #= 0
      dut.io.buttons2 #= 0
      dut.clockDomain.waitSampling(12)
      sleep(1)
      assert(dut.io.input.toLong == 0 && dut.io.input2.toLong == 0)
      for (_ <- 0 until 4) {
        dut.io.axes #= 1
        dut.clockDomain.waitSampling(2)
        dut.io.axes #= 0
        dut.clockDomain.waitSampling(2)
        sleep(1)
        assert(dut.io.input.toLong == 0)
      }
      dut.io.axes #= 2 + 4 + 16
      dut.io.buttons #= 0x81
      dut.io.buttons2 #= 0x21
      dut.clockDomain.waitSampling(14)
      sleep(1)
      assert(dut.io.input.toLong == 0x81010101L)
      assert(dut.io.input2.toLong == 0x21)
      dut.io.axes #= 63
      dut.clockDomain.waitSampling(14)
      sleep(1)
      assert(dut.io.input.toLong == 0x81000000L)
      dut.io.axes #= 1 + 8 + 32
      dut.io.buttons #= 0
      dut.io.buttons2 #= 0
      dut.clockDomain.waitSampling(14)
      sleep(1)
      assert(dut.io.input.toLong == 0x00ffffffL)
      dut.io.axes #= 0
      dut.clockDomain.waitSampling(14)
      sleep(1)
      assert(dut.io.input.toLong == 0)
      dut.clockDomain.assertReset()
      sleep(31)
      assert(dut.io.input.toLong == 0 && dut.io.input2.toLong == 0)
    }
  }
}
