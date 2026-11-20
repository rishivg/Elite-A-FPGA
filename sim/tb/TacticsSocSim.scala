package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase 5 checkpoint: AI tactics + two-sided combat.
  *
  * The scene seeds 10 static drifters and 2 hostile fighters. Hostiles use the firmware's
  * look_at_player AI each frame to face the player, close range, and roll dice against an
  * aggression byte to fire. Enemy fire draws a line from the firing ship's projected screen pos to
  * the view centre and decrements player_shields; on shields=0 the firmware flushes the bubble,
  * plays out an explosion, and respawns.
  *
  * Stick schedule mixes a couple of player fire bursts so we get the full two-sided story in the
  * 110-frame capture.
  */
class TacticsSocSim extends AnyFunSuite {
  test("hostile AI pursues and fires; player takes damage and respawns", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 110

    val schedule = Vector[(Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0),
      (12, 0, 0, 0, 1), // brief fire at whatever's in the crosshair
      (22, 0, 0, 0, 0),
      (45, 0, 0, 0, 1), // second burst
      (55, 0, 0, 0, 0),
      (80, 0, 0, 0, 0),
      (105, 0, 0, 0, 0)
    )
    def stickAt(f: Int): Int = {
      val (_, r, p, s, fire) = schedule.filter(_._1 <= f).last
      ((fire & 0xff) << 24) | ((s & 0xff) << 16) | ((p & 0xff) << 8) | (r & 0xff)
    }

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val frame = runner.capture(stickAt(f), 0, 5000000)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)

        val out = new File(f"build/sim/tactics_$f%03d.pbm")
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
      println(s"Tactics sim: $frames frames captured")
    }
  }
}
