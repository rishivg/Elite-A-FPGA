package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

class HyperspaceSocSim extends AnyFunSuite {
  test("chart -> select target -> hyperspace -> witchspace -> new system", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 110

    /** (firstFrame, roll, pitch, speed, btn3) — btn3 holds fire/chart/hyper bits. */
    // Three-frame holds with releases between actions exercise firmware edge
    // detection. InputCaptureSim separately checks retention of shorter presses.
    val schedule = Vector[(Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0x00),
      (17, 0, 0, 0, 0x02), // chart toggle pressed
      (20, 0, 0, 0, 0x00), // released
      (21, 1, 1, 0, 0x00), // nudge cursor +x, +y
      (30, -1, -1, 0, 0x00),
      (36, 0, 0, 0, 0x00),
      (38, 0, 0, 0, 0x04), // hyperspace pressed (held 3 frames)
      (41, 0, 0, 0, 0x00), // released
      (105, 0, 0, 0, 0x00)
    )
    def stickAt(f: Int): Int = {
      val (_, r, p, s, b3) = schedule.filter(_._1 <= f).last
      ((b3 & 0xff) << 24) | ((s & 0xff) << 16) | ((p & 0xff) << 8) | (r & 0xff)
    }

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(stickAt(f), 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)

        val out = new File(f"build/sim/hyper_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        val b3 = (stickAt(f) >> 24) & 0xff
        val dbg = frame.dbg & 0xffffffffL
        println(
          f"frame $f%03d  stick=0x${stickAt(f)}%08x  btn=0x$b3%02x  lit=$lit%4d  dbg=0x$dbg%08x  -> ${out.getName}"
        )

      }
      println(s"Hyperspace sim: $frames frames captured")
    }
  }
}
