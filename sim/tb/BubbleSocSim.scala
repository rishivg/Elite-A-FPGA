package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

class BubbleSocSim extends AnyFunSuite {
  test("local bubble runs 12 ships with independent motion", DemoFrames) {
    val W = 128
    val H = 128

    val frames = 120

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(0, 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)

        val out = new File(f"build/sim/bubble_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%03d  lit=$lit%4d  -> ${out.getName}")

      }
      println(s"Bubble sim: $frames frames captured")
    }
  }
}
