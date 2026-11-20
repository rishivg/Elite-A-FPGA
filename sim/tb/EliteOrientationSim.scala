package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteOrientation
import org.scalatest.funsuite.AnyFunSuite

class EliteOrientationSim extends AnyFunSuite {
  test("all raw coordinate encodings normalize like released LL17 and LL42", RenderKernel) {
    val rows = EliteFixture.load("ELITE_ORIENTATION_CASES", 37, 65536)
    assert(
      rows.size >= 65536 && rows.forall(r => r.length == 37 && r.forall(v => v >= 0 && v <= 255))
    )
    def packed(values: Seq[Int]) = values.zipWithIndex.map { case (v, i) =>
      BigInt(v) << (8 * i)
    }.sum
    SimConfig.compile(EliteOrientation()).doSim { dut =>
      SimTimeout(rows.size.toLong * 1000 + 10000)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.raw #= 0
      dut.io.transpose #= false
      dut.clockDomain.waitSampling(3)
      for ((row, index) <- rows.zipWithIndex) {
        dut.io.raw #= packed(row.take(18).toSeq)
        dut.io.transpose #= row(18) != 0
        assert(!dut.io.busy.toBoolean)
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        dut.io.raw #= 0
        dut.io.transpose #= row(18) == 0
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 100) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 100, s"Normalization timeout $index")
        assert(
          dut.io.result.toBigInt == packed(row.drop(19).toSeq),
          s"Original orientation bytes differ $index: ${row.mkString(",")}"
        )
        dut.clockDomain.waitSampling(2)
      }
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()

      dut.clockDomain.waitSampling(2)
      assert(!dut.io.busy.toBoolean && !dut.io.valid.toBoolean && dut.io.result.toBigInt == 0)
      println(s"Released raw orientation: ${rows.size} normalization cases passed")
    }
  }
}
