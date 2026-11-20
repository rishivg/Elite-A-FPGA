package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Stage C: the CPU animates the ship — computing a rotation matrix per frame and driving the
  * renderer over MMIO. Using the frame handshake (frameReady/frameAck), the testbench grabs each
  * finished frame, reads the framebuffer back, and dumps it. This is the full hardware/software
  * split running live.
  *
  * Prepare the shared native model (`make demo-build`). Run: `sbt "testOnly
  * leet.sim.RenderSocSim"`.
  */
class RenderSocSim extends AnyFunSuite {
  test("CPU animates the ship frame by frame over MMIO", DemoFrames) {
    val W = 128
    val H = 128

    val frames = 64 // full 360 deg rotation (firmware steps 1/64 turn per frame)
    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(0, 0, 3000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val out = new File(f"build/sim/soc_ship_$f%02d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%02d: $lit%4d px -> ${out.getName}")

      }
      println(s"CPU-driven animation: $frames frames captured")
    }
  }
}
