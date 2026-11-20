package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

class FlightSocSim extends AnyFunSuite {
  test("scripted stick flies the camera through a ship field", DemoFrames) {
    val W = 128
    val H = 128

    val frames = 96

    // Stick schedule: (firstFrame, roll, pitch, speed). Latest entry whose
    // firstFrame <= current frame wins. Roll: +right/-left. Pitch: +up/-down.
    val schedule = Vector[(Int, Int, Int, Int)](
      (0, 0, 0, 0), // cruise straight, take in the field
      (14, +1, 0, 0), // bank right (held -> ~45 deg over 8 frames)
      (22, 0, 0, 0), // release - orientation stays banked
      (30, 0, -1, 0), // nose down a touch to bring far ships into view
      (36, 0, 0, 0), // release
      (50, -1, 0, 0), // bank back left
      (58, 0, 0, 0), // release
      (66, 0, +1, 0), // pitch back up
      (72, 0, 0, +1), // accelerate
      (80, 0, 0, 0) // straight & quick to finish
    )
    def stickAt(f: Int): Int = {
      val (_, r, p, s) = schedule.filter(_._1 <= f).last
      val roll = r & 0xff
      val pitch = p & 0xff
      val speed = s & 0xff
      (speed << 16) | (pitch << 8) | roll
    }

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        // Apply this schedule step before waiting for the next completed frame;
        // hold it through the framebuffer read and acknowledgement.

        val frame = runner.capture(stickAt(f), 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)

        val out = new File(f"build/sim/flight_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%03d  stick=0x${stickAt(f)}%06x  lit=$lit%4d  -> ${out.getName}")

      }
      println(s"Flight sim: $frames frames captured")
    }
  }
}
