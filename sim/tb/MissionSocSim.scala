package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase P-F mission 1: hunt the Constrictor.
  *
  *   1. dbg == 0xCAFE — the boot sentinel now folds in mission_self_test (the mission state
  *      machine) plus the extended commander save (mission_state) via cmdr_self_test, so a green
  *      boot validates both. 2. The mission is offered at boot: a briefing ("PRIORITY MISSION /
  *      HUNT CONSTRICTOR / <system>") flashes over the top of the view, and the Constrictor is
  *      present in the home system's scene. We require the briefing band to light up early, hold
  *      fire to engage, and dump frames for visual inspection of the prototype hull.
  */
class MissionSocSim extends AnyFunSuite {
  test("boot self-test passes; mission briefing shows and the Constrictor is hunted", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 60

    def inAt(f: Int): Long = if (f >= 4) (1L << 24) else 0L // hold fire from frame 4

    val briefY = 4 until 34
    val briefX = 12 until 116
    def briefLit(fb: Array[Int]): Int =
      briefY.map(y => briefX.count(x => fb(y * W + x) != 0)).sum

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      var briefMax = 0
      for (f <- 0 until frames) {
        val frame = runner.capture(inAt(f), 0, 5000000)
        assert(
          frame.dbg == 0xcafeL,
          s"dbg=0x${frame.dbg.toHexString} at frame $f (mission self-test failed?)"
        )

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 50, s"frame $f nearly empty ($lit px)")
        if (f >= 2 && f <= 20) briefMax = math.max(briefMax, briefLit(fb))

        if (f % 8 == 0 || (f >= 2 && f <= 6)) {
          val out = new File(f"build/sim/mission_$f%03d.pbm")
          val pw = new PrintWriter(out)
          pw.println("P1")
          pw.println(s"$W $H")
          for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
          pw.close()
          println(
            f"frame $f%03d  fire=${if (inAt(f) != 0) 1 else 0}  lit=$lit%4d brief=${briefLit(fb)}%3d -> ${out.getName}"
          )
        }

      }
      // The mission briefing (three text rows) must light its band well above a
      // bare starfield.
      assert(briefMax > 120, s"mission briefing not seen (briefMax=$briefMax)")
      println(s"Mission sim: dbg=0xCAFE held; briefing peak=$briefMax")
    }
  }
}
