package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common.RenderConfig
import leet.render.Renderer
import org.scalatest.funsuite.AnyFunSuite

class RendererBoundsSim extends AnyFunSuite {
  test("offscreen face deltas preserve visibility and overflowing geometry cannot wrap onscreen") {
    val cfg = RenderConfig(screenW = 128, screenH = 128, viewH = 96, colorBits = 1)
    val vertices = Seq((-16.0, 0.0, 0.0), (16.0, 0.0, 0.0), (0.0, 8.0, 0.0))
    val faces = Seq((0, 2, 1), (0, 0, 0))
    val overflowing = Seq((51.2, 0.0, 0.0), (52.0, 0.0, 0.0), (51.6, 0.5, 0.0))
    SimConfig.compile(Renderer(cfg, models = Seq((vertices, faces), (overflowing, faces)))).doSim {
      dut =>
        SimTimeout(2000000)
        dut.clockDomain.forkStimulus(10)
        dut.io.start #= false
        dut.io.modelIdx #= 0
        dut.io.clearFirst #= true
        dut.io.lineStart #= false
        dut.io.circleStart #= false
        dut.io.fbRdAddr #= 0
        dut.io.lineP0.x #= 0
        dut.io.lineP0.y #= 0
        dut.io.lineP1.x #= 0
        dut.io.lineP1.y #= 0

        dut.io.lineColor #= 0
        dut.io.circleCenter.x #= 0
        dut.io.circleCenter.y #= 0
        dut.io.circleRadius #= 0

        dut.io.circleColor #= 0
        for (i <- 0 until 9) dut.io.m(i) #= (if (i % 4 == 0) 256 else 0)
        dut.io.t.x #= 0
        dut.io.t.y #= 0
        dut.io.t.z #= 512
        dut.clockDomain.waitSampling(3)
        dut.io.start #= true
        dut.clockDomain.waitSampling()

        dut.io.start #= false
        var guard = 0
        while (!dut.io.done.toBoolean && guard < 40000) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 40000)
        // Projection gives (-1216,48), (1344,48), (64,-592). The outward
        // triangle is visible, and only its horizontal edge crosses the viewport.
        // A 12-bit delta wraps 2560 to -1536 and wrongly flips its cull sign.
        for (y <- 0 until 128; x <- 0 until 128) {
          dut.io.fbRdAddr #= y * 128 + x
          dut.clockDomain.waitSampling(2)
          sleep(1)
          assert(dut.io.fbRdData.toInt == (if (y == 48) 1 else 0), s"unexpected pixel ($x,$y)")
        }
        // All second-model vertices lie far to the right. A 12-bit truncation
        // turns their x coordinates into 63,128,96 and creates a visible ghost.
        dut.io.modelIdx #= 1
        dut.io.start #= true
        dut.clockDomain.waitSampling()

        dut.io.start #= false
        guard = 0
        while (!dut.io.done.toBoolean && guard < 40000) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 40000)
        for (address <- 0 until cfg.pixels) {
          dut.io.fbRdAddr #= address
          dut.clockDomain.waitSampling(2)
          sleep(1)
          assert(dut.io.fbRdData.toInt == 0, s"overflowing ship wrapped into address $address")
        }
    }
  }
}
