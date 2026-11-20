package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase 4.2/8.2 combat activation: buy a missile, lock a target, fire it.
  *
  * Drives BOTH input words. Scenario: 0..6 cruise (a drifter sits dead-ahead -> auto target lock)
  * 7..30 open Equip, select MISSL, buy one missile, close Equip ~34 launch the missile (INPUT2
  * bit0) — it homes the locked ship and detonates after MISSILE_FLIGHT frames (explosion + kill)
  *
  * INPUT byte3: bit4 market, bit5 buy, bit7 equip. INPUT2 bit0: missile launch, bit1: energy bomb.
  */
class MissileSocSim extends AnyFunSuite {
  test("buy missile -> lock target -> launch -> detonate", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 58

    // (firstFrame, roll, pitch, speed, btn3, in2)
    val schedule = Vector[(Int, Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0x00, 0x00),
      (7, 0, 0, 0, 0x80, 0x00), // equip ON
      (10, 0, 0, 0, 0x00, 0x00),
      (12, 0, 1, 0, 0x00, 0x00), // select MISSL
      (20, 0, 0, 0, 0x20, 0x00), // BUY missile
      (23, 0, 0, 0, 0x00, 0x00),
      (28, 0, 0, 0, 0x80, 0x00), // equip OFF
      (31, 0, 0, 0, 0x00, 0x00),
      (34, 0, 0, 0, 0x00, 0x01), // MISSILE LAUNCH (INPUT2 bit0)
      (37, 0, 0, 0, 0x00, 0x00)
    )
    def inAt(f: Int): (Long, Long) = {
      val (_, r, p, s, b3, i2) = schedule.filter(_._1 <= f).last
      val in1 = (((b3.toLong & 0xff) << 24) | ((s.toLong & 0xff) << 16) |
        ((p.toLong & 0xff) << 8) | (r.toLong & 0xff)) & 0xffffffffL
      (in1, i2.toLong & 0xffffffffL)
    }

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      var audioMax = 0
      for (f <- 0 until frames) {
        val (in1, in2) = inAt(f)
        val frame = runner.capture(in1, in2, 5000000)
        audioMax = math.max(audioMax, frame.audio)

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val out = new File(f"build/sim/missile_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%03d  in2=0x$in2%02x  lit=$lit%4d  -> ${out.getName}")

      }
      // The PSG must have made noise (boot chord + the missile's explosion SFX).
      assert(audioMax > 0, s"PSG silent all run (audioSample max=$audioMax)")
      println(s"Missile sim: $frames frames captured, PSG audioSample peak=$audioMax")
    }
  }
}
