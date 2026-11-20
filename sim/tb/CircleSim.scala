package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common._
import leet.raster.BresenhamCircle
import org.scalatest.funsuite.AnyFunSuite
import scala.collection.mutable

/** Diff the HW Bresenham circle pixel stream against the Python oracle's algorithm (replicated here
  * in Scala so the test is hermetic). For a sweep of radii (1, 3, 5, 8, 16) the HW must emit
  * exactly the same multiset of pixels in the same order as the textbook integer midpoint
  * algorithm.
  *
  * Run: `sbt "testOnly leet.sim.CircleSim"`.
  */
class CircleSim extends AnyFunSuite {
  val cfg = RenderConfig(screenW = 128, screenH = 128, colorBits = 1)

  def golden(cx: Int, cy: Int, r: Int): Seq[(Int, Int)] = {
    if (r <= 0) return Seq((cx, cy))
    val out = mutable.ArrayBuffer.empty[(Int, Int)]
    var x = r
    var y = 0
    var err = 1 - r
    while (y <= x) {
      out += ((cx + x, cy + y))
      out += ((cx - x, cy + y))
      out += ((cx + x, cy - y))
      out += ((cx - x, cy - y))
      out += ((cx + y, cy + x))
      out += ((cx - y, cy + x))
      out += ((cx + y, cy - x))
      out += ((cx - y, cy - x))
      y += 1
      if (err < 0) err += 2 * y + 1
      else {
        x -= 1
        err += 2 * (y - x) + 1
      }
    }
    out.toSeq
  }

  test("HW Bresenham circle pixel stream matches oracle for r in {1,3,5,8,16}") {
    SimConfig.compile(BresenhamCircle(cfg)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      dut.io.start #= false
      dut.io.center.x #= 0
      dut.io.center.y #= 0
      dut.io.radius #= 0
      dut.io.color #= 1
      dut.clockDomain.waitSampling(3)

      def drawCircle(cx: Int, cy: Int, r: Int): Seq[(Int, Int)] = {
        dut.io.center.x #= cx
        dut.io.center.y #= cy
        dut.io.radius #= r
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        // Give the FSM one more sample so it's reliably in Draw with valid
        // pixel emerging on this edge.
        dut.clockDomain.waitSampling()
        val out = mutable.ArrayBuffer.empty[(Int, Int)]
        var guard = 0
        // Run until the algorithm exits — detect by sampling busy after each
        // edge. Capture pixel BEFORE waitSampling so we read the value the
        // FSM is presenting at the current state.
        while (guard < 5000) {
          val busy = dut.io.busy.toBoolean
          if (dut.io.pixel.valid.toBoolean) {
            out += ((dut.io.pixel.payload.x.toInt, dut.io.pixel.payload.y.toInt))
          }
          if (!busy) return out.toSeq
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 5000, s"circle r=$r stuck after $guard cycles")
        out.toSeq
      }

      for (r <- Seq(1, 3, 5, 8, 16)) {
        val got = drawCircle(60, 50, r)
        val exp = golden(60, 50, r)
        assert(
          got == exp,
          s"r=$r mismatch:\n  got (${got.size}): ${got.take(16)}...\n  exp (${exp.size}): ${exp.take(16)}..."
        )
        println(f"r=$r%-2d  ${got.size}%4d pixels  match")
      }
    }
  }
}
