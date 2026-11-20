package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteRenderRegisters
import org.scalatest.funsuite.AnyFunSuite

class EliteVertexRegistersSim extends AnyFunSuite {
  test(
    "raw orientation to original screen coordinates through production registers",
    RenderKernel
  ) {
    val rows = EliteFixture.load("ELITE_RAW_VERTICES", 69, 500)
    SimConfig.compile(EliteRenderRegisters()).doSim { dut =>
      SimTimeout(rows.size.toLong * 2400 + 20000)
      dut.clockDomain.forkStimulus(10)
      dut.io.write #= false
      dut.io.address #= 0
      dut.io.data #= 0
      dut.clockDomain.waitSampling(3)
      def write(address: Int, value: Long): Unit = {
        dut.io.address #= address
        dut.io.data #= value
        dut.io.write #= true
        dut.clockDomain.waitSampling()
        dut.io.write #= false
        sleep(1)
      }
      def read(address: Int): Long = {
        dut.io.address #= address
        sleep(1)
        dut.io.read.toLong
      }
      def word(values: Seq[Int]) = EliteFixture.word(values)
      def launch(row: Array[Int], flags: Int): Unit = {
        write(5, word(row.slice(18, 22).toSeq))
        write(6, word(row.slice(22, 24).toSeq))
        for (i <- 0 until 5) write(7 + i, word(row.slice(4 * i, (4 * i + 4) min 18).toSeq))
        for (i <- 0 until 3) write(15 + i, word(row.slice(24 + 4 * i, (28 + 4 * i) min 33).toSeq))
        write(12, flags)
      }
      for ((row, index) <- rows.zipWithIndex) {
        launch(row, if (index == 0) 31 else 29)
        assert(read(12) == 1)
        write(15, 0)
        write(17, 0)
        write(7, 0)
        write(5, 0)
        write(12, 29)
        if (index == 0) {
          write(0, 0x12345678)
          write(3, 1)
        }
        var guard = 0
        while ((read(12) & 2) == 0 && guard < 200) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 200 && read(12) == 2)
        for (i <- 0 until 5)
          assert(read(7 + i) == word(row.slice(33 + 4 * i, (37 + 4 * i) min 51).toSeq))
        for (i <- 0 until 3)
          assert(read(15 + i) == word(row.slice(24 + 4 * i, (28 + 4 * i) min 33).toSeq))
        assert(
          read(13) == word(row.slice(51, 55).toSeq) && read(14) == word(row.slice(55, 57).toSeq)
        )
        assert(
          read(18) == word(row.slice(57, 61).toSeq) && read(19) == word(row.slice(61, 65).toSeq),
          s"Reduced vertex $index"
        )
        assert(read(20) == word(row.slice(65, 69).toSeq), s"Screen vertex $index")
        if (index == 0)
          assert(
            read(0) == 0 && read(3) == 0,
            "Face bank must remain reserved until projection finishes"
          )
        // Out-of-map words must not alias the pose bank.
        write(31, 0xffffffffL)
        assert(read(31) == 0 && read(12) == 2)
      }
      // Abort after LL51 finishes but before the vertex stage completes.
      val row = rows.head
      launch(row, 29)
      var guard = 0
      while (read(13) != word(row.slice(51, 55).toSeq) && guard < 120) {
        dut.clockDomain.waitSampling()
        guard += 1
      }
      assert(guard < 120 && read(12) == 1)
      dut.clockDomain.assertReset()
      sleep(30)
      dut.clockDomain.deassertReset()

      dut.clockDomain.waitSampling(200)
      for (address <- 0 until 21) assert(read(address) == 0)
      println(s"Raw original screen pipeline: ${rows.size} vertices passed")
    }
  }
}
