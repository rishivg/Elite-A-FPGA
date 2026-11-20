package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

class MarketSocSim extends AnyFunSuite {
  test("open market -> move selection -> buy/sell -> close", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 60

    /** (firstFrame, roll, pitch, speed, btn3). */
    val schedule = Vector[(Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0x00),
      (7, 0, 0, 0, 0x10), // market toggle ON (held 3)
      (10, 0, 0, 0, 0x00), // released (market_mode latched on)
      (12, 0, 1, 0, 0x00), // pitch down: step the selection a few rows
      (20, 0, 0, 0, 0x20), // BUY (held 3)
      (23, 0, 0, 0, 0x00),
      (26, 0, 0, 0, 0x20), // BUY again
      (29, 0, 0, 0, 0x00),
      (32, 0, -1, 0, 0x00), // pitch up: move selection back
      (38, 0, 0, 0, 0x40), // SELL (held 3)
      (41, 0, 0, 0, 0x00),
      (50, 0, 0, 0, 0x10), // market toggle OFF
      (53, 0, 0, 0, 0x00)
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
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val out = new File(f"build/sim/market_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        val b3 = (stickAt(f) >> 24) & 0xff
        println(f"frame $f%03d  btn=0x$b3%02x  lit=$lit%4d  -> ${out.getName}")

      }
      println(s"Market sim: $frames frames captured")
    }
  }
}
