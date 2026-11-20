package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase P-D living world: asteroids in the scene, cargo canisters + scooping, and police that
  * arrive once you turn criminal.
  *
  *   1. dbg == 0xCAFE — the boot sentinel now folds in world_self_test (police heat by legal band +
  *      mineral yield by laser tier), so a green boot validates the living-world rules on the real
  *      CPU. 2. Scenario: cruise, then murder the trader sitting dead-ahead (hold fire), which
  *      makes us an Offender; over the next ~POLICE_CADENCE frames a police Viper is dispatched. We
  *      can't read firmware state from here, so we assert the world keeps running (no trap, frames
  *      stay populated) across the whole run and dump frames for visual inspection of the new
  *      hulls.
  */
class WorldSocSim extends AnyFunSuite {
  test("boot self-test passes; living world runs through a murder + manhunt", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 120

    // INPUT byte3 bit0 = fire. Murder the dead-ahead trader on frames 6..18.
    def inAt(f: Int): Long = if (f >= 6 && f <= 18) (1L << 24) else 0L

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      var minLit = 1 << 30
      for (f <- 0 until frames) {
        val frame = runner.capture(inAt(f), 0, 5000000)
        assert(
          frame.dbg == 0xcafeL,
          s"dbg=0x${frame.dbg.toHexString} at frame $f (world self-test failed?)"
        )

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 50, s"frame $f nearly empty ($lit px)")
        minLit = math.min(minLit, lit)

        if (f % 10 == 0 || (f >= 6 && f <= 20)) {
          val out = new File(f"build/sim/world_$f%03d.pbm")
          val pw = new PrintWriter(out)
          pw.println("P1")
          pw.println(s"$W $H")
          for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
          pw.close()
          println(
            f"frame $f%03d  fire=${if (inAt(f) != 0) 1 else 0}  lit=$lit%4d -> ${out.getName}"
          )
        }

      }
      println(s"World sim: $frames frames, dbg=0xCAFE held, min lit=$minLit")
    }
  }
}
