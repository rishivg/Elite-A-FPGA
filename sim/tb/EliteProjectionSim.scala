package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteProjection
import org.scalatest.funsuite.AnyFunSuite

class EliteProjectionSim extends AnyFunSuite {
  test("every byte numerator/divisor pair projects like released LL60", RenderKernel) {
    val rows = EliteFixture.load("ELITE_PROJECTION_CASES", 12, 65536)
    SimConfig.compile(EliteProjection()).doSim { dut =>
      SimTimeout(rows.size.toLong * 1000 + 10000)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.reduced #= 0
      dut.clockDomain.waitSampling(3)
      for ((row, index) <- rows.zipWithIndex) {
        dut.io.reduced #= EliteFixture.packed(row.take(8).toSeq)
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        dut.io.reduced #= 0
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 70) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 70, s"Projection timeout $index")
        assert(
          dut.io.result.toBigInt == EliteFixture.packed(row.drop(8).toSeq),
          s"Original projection $index: ${row.mkString(",")}"
        )
        dut.clockDomain.waitSampling(2)
      }
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()

      dut.clockDomain.waitSampling(2)
      assert(!dut.io.busy.toBoolean && dut.io.result.toBigInt == 0)
      println(s"Released projection: ${rows.size} cases passed")
    }
  }
}
