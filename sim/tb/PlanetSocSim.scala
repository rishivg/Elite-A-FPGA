package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase 6.1 checkpoint: HW Bresenham circle primitive feeding a firmware planet that grows as the
  * player closes range, then respawns far when it passes the near plane. No player input — just
  * observe the bubble + chrome + planet (and the Phase 5 hostile AI continuing to fire on the
  * player).
  */
class PlanetSocSim extends AnyFunSuite {
  test("HW circle + firmware planet grows over time as player closes", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 120

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(0, 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)

        val out = new File(f"build/sim/planet_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%03d  lit=$lit%4d  -> ${out.getName}")

      }
      println(s"Planet sim: $frames frames captured")
    }
  }
}
