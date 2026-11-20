package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common._
import leet.raster.Bresenham
import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Drives the Bresenham engine to draw a box + both diagonals into a software framebuffer, prints
  * the result as ASCII to stdout, and dumps build/sim/lines.pbm. Run with `sbt "testOnly
  * leet.sim.BresenhamSim"`.
  *
  * Drawing a box and both diagonals exercises every octant (shallow/steep, both directions), so a
  * clean figure here means the stepping logic is correct.
  */
class BresenhamSim extends AnyFunSuite {
  test("rasterises a box + diagonals into a framebuffer") {
    val cfg = RenderConfig(screenW = 64, screenH = 48, colorBits = 1)
    SimConfig.compile(Bresenham(cfg)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      val W = cfg.screenW
      val H = cfg.screenH
      val fb = Array.fill(H, W)(0)

      // Drive inputs to known values and let the reset deassert before the first
      // transaction (otherwise the very first start pulse is swallowed by reset).
      dut.io.start #= false
      dut.io.color #= 0
      dut.io.p0.x #= 0
      dut.io.p0.y #= 0
      dut.io.p1.x #= 0
      dut.io.p1.y #= 0
      dut.clockDomain.waitSampling(5)

      def drawLine(x0: Int, y0: Int, x1: Int, y1: Int): Unit = {
        dut.io.p0.x #= x0
        dut.io.p0.y #= y0
        dut.io.p1.x #= x1
        dut.io.p1.y #= y1
        dut.io.color #= 1
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false

        var guard = 0
        do {
          if (dut.io.pixel.valid.toBoolean) {
            val px = dut.io.pixel.payload.x.toInt
            val py = dut.io.pixel.payload.y.toInt
            if (px >= 0 && px < W && py >= 0 && py < H) fb(py)(px) = 1
          }
          dut.clockDomain.waitSampling()
          guard += 1
        } while (dut.io.busy.toBoolean && guard < 20000)
        assert(guard < 20000, "line did not terminate — Bresenham stuck")
      }

      val (l, t, r, b) = (5, 5, 58, 42)
      drawLine(l, t, r, t) // top
      drawLine(r, t, r, b) // right
      drawLine(r, b, l, b) // bottom
      drawLine(l, b, l, t) // left
      drawLine(l, t, r, b) // diagonal '\'
      drawLine(r, t, l, b) // diagonal '/'

      // --- ASCII preview to stdout ---
      println(s"\nBresenham output (${W}x$H):")
      for (yy <- 0 until H) {
        val sb = new StringBuilder
        for (xx <- 0 until W) sb.append(if (fb(yy)(xx) == 1) '#' else ' ')
        println(sb.toString)
      }

      // --- PBM dump (P1) for tools/pbm2png.py ---
      val out = new File("build/sim/lines.pbm")
      out.getParentFile.mkdirs()
      val pw = new PrintWriter(out)
      pw.println("P1")
      pw.println(s"$W $H")
      for (yy <- 0 until H) pw.println((0 until W).map(fb(yy)(_)).mkString(" "))
      pw.close()
      println(s"wrote ${out.getPath}")

      // --- checks: corners + crossing + non-trivial coverage ---
      assert(fb(t)(l) == 1 && fb(b)(r) == 1, "corners not drawn")
      assert(fb(b - 1)(l) == 1, "left edge not drawn")
      val crossing = (22 to 24).exists(yy => (30 to 33).exists(xx => fb(yy)(xx) == 1))
      assert(crossing, "diagonals did not cross near centre")
      val lit = fb.map(_.sum).sum
      assert(lit > 150, s"too few pixels lit ($lit)")
      println(s"lit pixels: $lit")
    }
  }

  /** Regression for the clip-by-reject correctness fix: a line whose endpoints are far off-screen
    * on opposite sides (its on-screen span is far from p0). With the old narrow Pixel type the
    * endpoints wrapped, and the old watchdog (2*(W+H)) truncated before reaching the visible span —
    * leaving gaps. The full on-screen row must now be rasterised.
    */
  test("clips an over-long line without truncation or wrap") {
    val cfg = RenderConfig(screenW = 64, screenH = 48, colorBits = 1)
    SimConfig.compile(Bresenham(cfg)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      dut.io.start #= false
      dut.io.color #= 0
      dut.io.p0.x #= 0
      dut.io.p0.y #= 0
      dut.io.p1.x #= 0
      dut.io.p1.y #= 0
      dut.clockDomain.waitSampling(5)

      val W = cfg.screenW
      val H = cfg.screenH
      val row = 24
      val onScreen = Array.fill(W)(false)

      dut.io.p0.x #= -300
      dut.io.p0.y #= row // far off the left
      dut.io.p1.x #= 300
      dut.io.p1.y #= row // far off the right
      dut.io.color #= 1
      dut.io.start #= true
      dut.clockDomain.waitSampling()
      dut.io.start #= false
      var guard = 0
      do {
        if (dut.io.pixel.valid.toBoolean) {
          val px = dut.io.pixel.payload.x.toInt
          val py = dut.io.pixel.payload.y.toInt
          if (px >= 0 && px < W && py == row) onScreen(px) = true
        }
        dut.clockDomain.waitSampling()
        guard += 1
      } while (dut.io.busy.toBoolean && guard < 40000)
      assert(guard < 40000, "line did not terminate")

      val missing = (0 until W).filterNot(onScreen(_))
      assert(missing.isEmpty, s"on-screen span has gaps at x=${missing.mkString(",")}")
      println(s"over-long line: full on-screen row [$row] rasterised, no truncation")
    }
  }
}
