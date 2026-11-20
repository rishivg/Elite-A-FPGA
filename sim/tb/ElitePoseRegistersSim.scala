package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.render.elite.EliteRenderRegisters
import org.scalatest.funsuite.AnyFunSuite

class ElitePoseRegistersSim extends AnyFunSuite {
  test(
    "original viewer transforms feed faces atomically with busy and reset semantics",
    RenderKernel
  ) {
    val rows = EliteFixture.load("ELITE_PIPELINE_CASES", 40, 40)
    assert(rows.size >= 40 && rows.forall(r => r.length == 40 && r.forall(v => v >= 0 && v <= 255)))
    def packed(values: Seq[Int]) = values.zipWithIndex.map { case (v, i) =>
      v.toLong << (8 * i)
    }.sum
    def settings(row: Array[Int]) = row(28).toLong << 8 | row(29).toLong << 16
    def signs(row: Array[Int]) = (0 until 3).map(i => (row(31 + 2 * i) >> 7) << i).sum
    SimConfig.compile(EliteRenderRegisters()).doSim { dut =>
      SimTimeout(rows.size.toLong * 1000 + 10000)
      dut.clockDomain.forkStimulus(10)
      dut.io.write #= false
      dut.io.address #= 0
      dut.io.data #= 0
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
      def complete(addr: Int): Unit = {
        var guard = 0
        while ((read(addr) & 2) == 0 && guard < 20) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 20 && read(addr) == 2)
      }
      for ((row, index) <- rows.zipWithIndex) {
        write(0, packed(row.slice(24, 28).toSeq))
        write(1, 0xabcdef)
        write(2, settings(row) | 7)
        write(5, packed(row.take(4).toSeq))
        write(6, packed(row.slice(4, 6).toSeq))
        for (word <- 0 until 5)
          write(7 + word, packed(row.slice(6 + 4 * word, (10 + 4 * word) min 24).toSeq))
        write(12, 3)
        assert(read(12) == 1)
        write(5, 0)
        write(0, 0)
        write(3, 1)
        write(12, 3)
        complete(12)
        assert(
          read(13) == packed(row.slice(30, 34).toSeq) && read(14) == packed(
            row.slice(34, 36).toSeq
          ),
          s"LL51 output $index"
        )
        assert(read(1) == packed(Seq(row(30), row(32), row(34))))
        assert(read(2) == (settings(row) | signs(row)))
        assert(read(0) == packed(row.slice(24, 28).toSeq) && read(5) == packed(row.take(4).toSeq))
        assert(read(3) == 0, "Face start during the reserved transform must be ignored")
        write(3, 1)
        complete(3)
        assert(
          read(4) == row(36) && read(1) == packed(row.slice(37, 40).toSeq),
          s"connected face output $index"
        )
        // Starting another transfer while a face request is executing is ignored.
        if (index == 0) {
          write(3, 1)
          write(12, 3)
          assert(read(12) == 2)
          complete(3)
        }
      }
      // Without transfer, the two stages may execute independently. Preserve
      // face operands/results when LL51 completes.
      val row = rows.last
      write(1, packed(Seq(row(30), row(32), row(34))))
      write(3, 1)
      write(12, 1)
      complete(12)
      complete(3)
      assert(read(4) == row(36) && read(1) == packed(row.slice(37, 40).toSeq))
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()

      dut.clockDomain.waitSampling(2)
      for (addr <- Seq(0, 1, 2, 3, 5, 6, 7, 11, 12, 13, 14)) assert(read(addr) == 0)
      println(s"Connected original LL51/LL86: ${rows.size} pipeline cases passed")
    }
  }
}
