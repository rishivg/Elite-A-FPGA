package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase 4 checkpoint: piloted flight + lasers + explosions over the existing Phase 2 bubble +
  * Phase 3 chrome.
  *
  * Schedule: brief cruise, fire burst at the ship dead ahead, watch the cloud, bank toward another
  * target, fire again, cruise out. ship 0 starts at (0, 0, Q(22)) so the first fire burst is
  * guaranteed to land in the crosshair (no banking required to demonstrate the hit/explode path).
  *
  * Input packing (firmware-side): byte0 roll, byte1 pitch, byte2 speed, byte3 bit0 fire.
  */
class CombatSocSim extends AnyFunSuite {
  test("scripted fire bursts hit, explode, and cool down", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 80

    /** (firstFrame, roll, pitch, speed, fire). Latest entry whose firstFrame <= current wins.
      */
    val schedule = Vector[(Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0), // cruise, take in scene
      (5, 0, 0, 0, 1), // fire at ship 0 dead ahead
      (16, 0, 0, 0, 0), // release: cool + watch particles
      (34, -1, 0, 0, 0), // bank left toward an off-axis target
      (42, 0, 0, 0, 0), // hold
      (50, 0, 0, 0, 1), // fire again
      (62, 0, 0, 0, 0), // cool
      (78, 0, 0, 0, 0)
    )
    def stickAt(f: Int): Int = {
      val (_, r, p, s, fire) = schedule.filter(_._1 <= f).last
      val roll = r & 0xff
      val pitch = p & 0xff
      val speed = s & 0xff
      val btn = fire & 0xff
      (btn << 24) | (speed << 16) | (pitch << 8) | roll
    }

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(stickAt(f), 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)

        val out = new File(f"build/sim/combat_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        val fire = (stickAt(f) >> 24) & 1
        println(
          f"frame $f%03d  stick=0x${stickAt(f)}%08x  fire=$fire  lit=$lit%4d  -> ${out.getName}"
        )

      }
      println(s"Combat sim: $frames frames captured")
    }
  }
}
