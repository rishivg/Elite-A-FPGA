package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteFaceRegisters
import org.scalatest.funsuite.AnyFunSuite

class EliteFaceRegistersSim extends AnyFunSuite {
  test(
    "busy requests preserve operands, overflow dots carry, and reset clears completion",
    RenderKernel
  ) {
    val rows = EliteFixture.load("ELITE_FACE_CASES", 14, 10000)
    val chain = rows
      .sliding(2)
      .find(pair =>
        pair(0).slice(4, 7).toSeq != pair(0).slice(11, 14).toSeq &&
          pair(0).slice(11, 14).toSeq == pair(1).slice(4, 7).toSeq &&
          pair(0).slice(7, 10).toSeq == pair(1).slice(7, 10).toSeq
      )
      .getOrElse(throw new IllegalArgumentException("Original overflow chain missing"))
    def packed(values: Seq[Int]) = values.zipWithIndex.map { case (v, i) =>
      v.toLong << (8 * i)
    }.sum
    SimConfig.compile(EliteFaceRegisters()).doSim { dut =>
      SimTimeout(20000)
      dut.clockDomain.forkStimulus(10)
      dut.io.write #= false
      dut.io.address #= 0
      dut.io.data #= 0
      dut.io.reserved #= false
      dut.io.loadDots #= false
      dut.io.loadedDots #= 0

      dut.io.loadedSigns #= 0
      dut.clockDomain.waitSampling(3)
      def write(addr: Int, value: Long): Unit = {
        dut.io.address #= addr
        dut.io.data #= value
        dut.io.write #= true
        dut.clockDomain.waitSampling()
        dut.io.write #= false
        sleep(1)
      }
      def read(addr: Int): Long = {
        dut.io.address #= addr
        sleep(1)
        dut.io.read.toLong
      }
      for ((row, index) <- chain.zipWithIndex) {
        write(0, packed(row.take(4).toSeq))
        if (index == 0) {
          write(1, packed(row.slice(4, 7).toSeq))
          write(2, row(7) | (row(8) << 8) | (row(9) << 16))
        }
        write(3, 1)
        assert((read(3) & 1) == 1 && (read(3) & 2) == 0)
        // These writes and a duplicate start arrive while the kernel is busy.
        write(0, 0)
        write(1, 0)
        write(3, 1)
        var guard = 0
        while ((read(3) & 2) == 0 && guard < 20) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 20)
        assert(read(4) == row(10))
        assert(read(1) == packed(row.slice(11, 14).toSeq))
        assert(read(0) == packed(row.take(4).toSeq))
        assert(read(3) == 2)
        write(3, 0)
        assert(read(3) == 2)
      }
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()
      dut.clockDomain.waitSampling(2)
      assert(read(3) == 0 && read(1) == 0 && read(0) == 0)
    }
  }
}
