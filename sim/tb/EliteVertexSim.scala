package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteVertex
import org.scalatest.funsuite.AnyFunSuite

class EliteVertexSim extends AnyFunSuite {
  test("world translation, shared scaling and projection match released LL9", RenderKernel) {
    val rows = EliteFixture.load("ELITE_VERTEX_CASES", 27, 65536)
    SimConfig.compile(EliteVertex()).doSim { dut =>
      SimTimeout(rows.size.toLong * 1000 + 10000)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.world #= 0
      dut.io.rotated #= 0
      dut.clockDomain.waitSampling(3)
      for ((row, index) <- rows.zipWithIndex) {
        dut.io.world #= EliteFixture.packed(row.take(9).toSeq)
        dut.io.rotated #= EliteFixture.packed(row.slice(9, 15).toSeq)
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        dut.io.world #= 0
        dut.io.rotated #= 0
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 85) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 85, s"Vertex timeout $index")
        assert(
          dut.io.reduced.toBigInt == EliteFixture.packed(row.slice(15, 23).toSeq),
          s"Original translation/scaling $index: ${row.mkString(",")}"
        )
        assert(
          dut.io.result.toBigInt == EliteFixture.packed(row.drop(23).toSeq),
          s"Original screen bytes $index: ${row.mkString(",")}"
        )
        dut.clockDomain.waitSampling(2)
      }
      println(s"Released vertex: ${rows.size} cases passed")
    }
  }
}
