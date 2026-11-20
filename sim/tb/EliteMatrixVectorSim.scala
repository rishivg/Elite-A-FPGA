package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteMatrixVector
import org.scalatest.funsuite.AnyFunSuite

class EliteMatrixVectorSim extends AnyFunSuite {
  test("all six LL51 result bytes match the original 6502 routine", RenderKernel) {
    val rows = EliteFixture.load("ELITE_MATRIX_CASES", 30, 65536)
    assert(
      rows.size >= 65536 && rows.forall(r => r.length == 30 && r.forall(v => v >= 0 && v <= 255))
    )
    def packed(bytes: Seq[Int]) = bytes.zipWithIndex.map { case (v, i) => BigInt(v) << (8 * i) }.sum
    SimConfig.compile(EliteMatrixVector()).doSim { dut =>
      SimTimeout(rows.size.toLong * 200)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.vector #= 0
      dut.io.matrix #= 0
      dut.clockDomain.waitSampling(3)
      for ((row, index) <- rows.zipWithIndex) {
        dut.io.vector #= packed(row.take(6).toSeq)
        dut.io.matrix #= packed(row.slice(6, 24).toSeq)
        assert(!dut.io.busy.toBoolean)
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        dut.io.vector #= 0
        dut.io.matrix #= 0 // Snapshot ownership after acceptance.
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 15) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 15, s"LL51 timeout in case $index")
        assert(
          dut.io.result.toBigInt == packed(row.drop(24).toSeq),
          s"LL51 bytes differ in case $index: ${row.mkString(",")}"
        )
        dut.clockDomain.waitSampling(2)
      }
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()

      dut.clockDomain.waitSampling(2)
      assert(!dut.io.busy.toBoolean && !dut.io.valid.toBoolean && dut.io.result.toBigInt == 0)
      println(s"Released LL51: ${rows.size} transforms matched all six bytes")
    }
  }
}
