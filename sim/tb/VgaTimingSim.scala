package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.video.VgaTiming
import leet.common.VideoTiming
import org.scalatest.funsuite.AnyFunSuite

class VgaTimingSim extends AnyFunSuite {
  test("pixel enables, sync polarities, blanking and frame cadence are exact") {
    val timing = VideoTiming(
      hActive = 8,
      hFront = 2,
      hSync = 2,
      hBack = 2,
      vActive = 6,
      vFront = 1,
      vSync = 1,
      vBack = 2
    )
    for (divider <- Seq(1, 4)) {
      SimConfig.compile(VgaTiming(timing, divider)).doSim { dut =>
        SimTimeout(50000)
        dut.clockDomain.forkStimulus(10)
        dut.clockDomain.waitSampling(3)
        dut.clockDomain.assertReset()
        sleep(31)
        dut.clockDomain.deassertReset()
        sleep(1)
        var blanks = 0
        for (cycle <- 0 until timing.hTotal * timing.vTotal * divider * 2) {
          val pixel = cycle / divider
          val x = pixel % timing.hTotal
          val y = pixel / timing.hTotal % timing.vTotal
          assert(
            dut.io.active.toBoolean == (x < 8 && y < 6),
            s"active divider=$divider cycle=$cycle"
          )
          assert(dut.io.x.toInt == x % 8 && dut.io.y.toInt == y % 8)
          assert(dut.io.hsync.toBoolean == !(x >= 10 && x < 12))
          assert(dut.io.vsync.toBoolean == (y != 7))
          val blank = cycle % divider == divider - 1 && x == 0 && y == 6
          assert(dut.io.blankStart.toBoolean == blank, s"blank divider=$divider cycle=$cycle")
          if (blank) blanks += 1
          dut.clockDomain.waitSampling()
          sleep(1)
        }
        assert(blanks == 2)
      }
    }
  }
}
