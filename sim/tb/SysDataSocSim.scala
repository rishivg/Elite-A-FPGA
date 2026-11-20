package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase 8 nav extension: chart-mode cycle + the system-data screen.
  *
  * The chart button (input byte 3 bit 1) now cycles space -> galactic chart -> system data ->
  * space. This sim presses it twice to land on the system-data screen (economy / government /
  * population / productivity of the selected target system), holds it, then cycles back to flight.
  *
  * Galactic hyperdrive (galaxy++ on a consumed fit) is proven by tools/oracle/galaxy.py — a fresh
  * commander can't afford the 5000 Cr drive, so it isn't exercised live here.
  */
class SysDataSocSim extends AnyFunSuite {
  test("chart cycle -> system-data screen -> back to flight", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 46

    val schedule = Vector[(Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0x00),
      (7, 0, 0, 0, 0x02), // chart press 1 -> galactic chart (held 3)
      (10, 0, 0, 0, 0x00),
      (14, 0, 0, 0, 0x02), // chart press 2 -> system data
      (17, 0, 0, 0, 0x00),
      (36, 0, 0, 0, 0x02), // chart press 3 -> back to space
      (39, 0, 0, 0, 0x00)
    )
    def stickAt(f: Int): Long = {
      val (_, r, p, s, b3) = schedule.filter(_._1 <= f).last
      (((b3.toLong & 0xff) << 24) | ((s.toLong & 0xff) << 16) |
        ((p.toLong & 0xff) << 8) | (r.toLong & 0xff)) & 0xffffffffL
    }

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(stickAt(f), 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val out = new File(f"build/sim/sysdata_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        val b3 = (stickAt(f) >> 24) & 0xff
        println(f"frame $f%03d  btn=0x$b3%02x  lit=$lit%4d  -> ${out.getName}")

      }
      println(s"SysData sim: $frames frames captured")
    }
  }
}
