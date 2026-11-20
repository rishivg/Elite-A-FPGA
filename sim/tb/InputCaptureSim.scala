package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.soc.InputCapture
import org.scalatest.funsuite.AnyFunSuite

class InputCaptureSim extends AnyFunSuite {
  test("short buttons survive rendering, reads consume once, held axes remain current") {
    SimConfig.compile(InputCapture()).doSim { dut =>
      SimTimeout(10000)
      dut.clockDomain.forkStimulus(10)
      dut.io.input #= 0
      dut.io.input2 #= 0
      dut.io.consume #= false
      dut.io.consume2 #= false
      dut.clockDomain.waitSampling(3)
      dut.io.input #= 0x200001ffL
      dut.io.input2 #= 4
      dut.clockDomain.waitSampling(3)
      dut.io.input #= 0
      dut.io.input2 #= 0
      dut.clockDomain.waitSampling(12)
      sleep(1)
      assert(dut.io.word.toLong == 0x20000000L && dut.io.word2.toLong == 4)
      dut.io.consume #= true
      dut.clockDomain.waitSampling()
      sleep(1)
      dut.io.consume #= false
      assert(dut.io.word.toLong == 0 && dut.io.word2.toLong == 4)
      dut.io.consume2 #= true
      dut.clockDomain.waitSampling()
      sleep(1)
      dut.io.consume2 #= false
      assert(dut.io.word2.toLong == 0)
      // A held button is visible on every read, and released axes never stick.
      dut.io.input #= 0x01010001L
      dut.clockDomain.waitSampling(3)
      dut.io.consume #= true
      dut.clockDomain.waitSampling()

      sleep(1)
      assert(dut.io.word.toLong == 0x01010001L)
      dut.io.input #= 0
      dut.clockDomain.waitSampling(3)
      sleep(1)
      dut.io.consume #= false
      assert(dut.io.word.toLong == 0)
      // Simultaneous independent button words retain every bit.
      dut.io.input #= 0x81000000L
      dut.io.input2 #= 0x21
      dut.clockDomain.waitSampling(3)
      dut.io.input #= 0
      dut.io.input2 #= 0

      dut.clockDomain.waitSampling(3)
      sleep(1)
      assert(dut.io.word.toLong == 0x81000000L && dut.io.word2.toLong == 0x21)
      dut.clockDomain.assertReset()
      sleep(31)
      assert(dut.io.word.toLong == 0 && dut.io.word2.toLong == 0)
    }
  }
}
