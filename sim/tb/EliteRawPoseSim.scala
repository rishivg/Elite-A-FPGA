package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteRenderRegisters
import org.scalatest.funsuite.AnyFunSuite

class EliteRawPoseSim extends AnyFunSuite {
  test(
    "raw orientations reach original viewer, vertex and face results through MMIO",
    RenderKernel
  ) {
    val transforms = EliteFixture.load("ELITE_RAW_TRANSFORMS", 49, 600)
    val faces = EliteFixture.load("ELITE_RAW_PIPELINES", 58, 40)
    assert(transforms.size >= 600 && transforms.count(_(18) == 1) >= 400 && faces.size >= 40)
    def packed(values: Seq[Int]) = values.zipWithIndex.map { case (v, i) =>
      v.toLong << (8 * i)
    }.sum
    SimConfig.compile(EliteRenderRegisters()).doSim { dut =>
      SimTimeout((transforms.size + faces.size).toLong * 1600 + 20000)
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
        while ((read(addr) & 2) == 0 && guard < 120) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 120 && read(addr) == 2)
      }
      def operands(raw: Seq[Int], vector: Seq[Int]): Unit = {
        write(5, packed(vector.take(4)))
        write(6, packed(vector.drop(4)))
        for (word <- 0 until 5) write(7 + word, packed(raw.slice(4 * word, (4 * word + 4) min 18)))
      }
      def matrix(expected: Seq[Int]): Unit = {
        for (word <- 0 until 5)
          assert(read(7 + word) == packed(expected.slice(4 * word, (4 * word + 4) min 18)))
      }
      for ((row, index) <- transforms.zipWithIndex) {
        operands(row.take(18).toSeq, row.slice(19, 25).toSeq)
        write(12, 5 | (row(18) << 3))
        assert(read(12) == 1)
        write(7, 0)
        write(5, 0)
        write(12, 5)
        complete(12)
        matrix(row.slice(25, 43).toSeq)
        assert(
          read(13) == packed(row.slice(43, 47).toSeq) && read(14) == packed(
            row.slice(47, 49).toSeq
          ),
          s"raw transform $index"
        )
        assert(read(5) == packed(row.slice(19, 23).toSeq))
      }
      for ((row, index) <- faces.zipWithIndex) {
        write(0, packed(row.slice(42, 46).toSeq))
        write(1, 0xabcdef)
        val settings = (row(46).toLong << 8) | (row(47).toLong << 16)
        write(2, settings | 7)
        operands(row.take(18).toSeq, row.slice(18, 24).toSeq)
        write(12, 7)
        write(0, 0)
        write(3, 1)
        write(7, 0)
        // The normalized matrix becomes visible atomically before LL51
        // finishes. Face reservation must persist through that launch phase.
        if (index == 0 && packed(row.take(4).toSeq) != packed(row.slice(24, 28).toSeq)) {
          var guard = 0
          while (read(7) != packed(row.slice(24, 28).toSeq) && guard < 100) {
            dut.clockDomain.waitSampling()
            guard += 1
          }
          assert(guard < 100 && read(12) == 1)
          matrix(row.slice(24, 42).toSeq)
          write(3, 1)
        }
        complete(12)
        matrix(row.slice(24, 42).toSeq)
        assert(
          read(13) == packed(row.slice(48, 52).toSeq) && read(14) == packed(row.slice(52, 54).toSeq)
        )
        val signs = (0 until 3).map(i => (row(49 + 2 * i) >> 7) << i).sum
        assert(read(2) == (settings | signs) && read(1) == packed(Seq(row(48), row(50), row(52))))
        assert(read(0) == packed(row.slice(42, 46).toSeq) && read(3) == 0)
        write(3, 1)
        complete(3)
        assert(
          read(4) == row(54) && read(1) == packed(row.slice(55, 58).toSeq),
          s"raw face pipeline $index"
        )
      }
      // Abort normalization before completion; no late result or transfer may
      // reappear after reset.
      operands(transforms.head.take(18).toSeq, transforms.head.slice(19, 25).toSeq)
      write(12, 7)
      dut.clockDomain.waitSampling(2)
      assert(read(12) == 1)
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()

      dut.clockDomain.waitSampling(150)
      for (addr <- Seq(0, 1, 2, 3, 5, 6, 7, 11, 12, 13, 14)) assert(read(addr) == 0)
      println(
        s"Raw original pipelines: ${transforms.size} transforms and ${faces.size} face cases passed"
      )
    }
  }
}
