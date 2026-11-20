package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common.{RenderConfig, VideoTiming}
import leet.video.{ScaledScanout, VgaTiming}
import org.scalatest.funsuite.AnyFunSuite

case class ScanoutHarness(cfg: RenderConfig, timing: VideoTiming, divider: Int) extends Component {
  val io = new Bundle {
    val address = out UInt (log2Up(cfg.pixels) bits)
    val inside = out Bool ()
  }
  val vga = VgaTiming(timing, divider)
  val scan = ScaledScanout(cfg, timing)
  scan.io.x := vga.io.x
  scan.io.y := vga.io.y
  scan.io.active := vga.io.active
  scan.io.rowActive := vga.io.rowActive
  scan.io.pixelTick := vga.io.pixelTick
  scan.io.lineEnd := vga.io.lineEnd
  io.address := scan.io.address
  io.inside := scan.io.inside
}

class ScaledScanoutSim extends AnyFunSuite {
  test("counter scanout matches integer scaling across borders, enables and reset") {
    val cfg = RenderConfig(screenW = 8, screenH = 8, colorBits = 1)
    for ((width, height, divider) <- Seq((8, 8, 1), (18, 16, 4), (34, 28, 1), (40, 43, 4))) {
      val timing = VideoTiming(
        hActive = width,
        hFront = 3,
        hSync = 2,
        hBack = 5,
        vActive = height,
        vFront = 2,
        vSync = 2,
        vBack = 5
      )
      val scale = (width / 8) min (height / 8)
      val left = (width - 8 * scale) / 2

      val top = (height - 8 * scale) / 2
      SimConfig.compile(ScanoutHarness(cfg, timing, divider)).doSim { dut =>
        SimTimeout(1000000)
        dut.clockDomain.forkStimulus(10)
        dut.clockDomain.waitSampling(3)
        def restart(): Unit = {
          dut.clockDomain.assertReset()
          sleep(31)
          dut.clockDomain.deassertReset()
          sleep(1)
        }
        def check(frames: Int): Unit = {
          for (cycle <- 0 until timing.hTotal * timing.vTotal * divider * frames) {
            val pixel = cycle / divider
            val x = pixel % timing.hTotal

            val y = pixel / timing.hTotal % timing.vTotal
            val inside = x >= left && x < left + 8 * scale && y >= top && y < top + 8 * scale
            assert(
              dut.io.inside.toBoolean == inside,
              s"inside scale=$scale divider=$divider ($x,$y)"
            )
            if (inside)
              assert(
                dut.io.address.toInt == ((y - top) / scale) * 8 + (x - left) / scale,
                s"address ($x,$y)"
              )
            dut.clockDomain.waitSampling()
            sleep(1)
          }
        }
        restart()
        check(2)
        dut.clockDomain.waitSampling(143)
        restart()
        check(1)
      }
    }
  }
}
