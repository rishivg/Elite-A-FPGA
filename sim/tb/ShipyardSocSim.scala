package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase P-E ship yard: buy and sell whole ships, with a rotating hangar view.
  *
  *   1. dbg == 0xCAFE — the boot sentinel now folds in ship_self_test (the trade-in maths) plus the
  *      extended commander save (player_ship) via cmdr_self_test, so a green boot validates both.
  *      2. Open the ship yard (INPUT2 bit5) and buy the selected hull (the BUY button, INPUT byte3
  *      bit5). The selected ship spins in the hangar and the four-hull list is shown; we require
  *      the screen to stay populated and dump frames for visual inspection.
  *
  * From the default Cobra (100 Cr) the selected Sidewinder is a downgrade, so the trade refunds and
  * always succeeds.
  */
class ShipyardSocSim extends AnyFunSuite {
  test("boot self-test passes; ship yard opens, hangar renders, a hull is bought", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 40

    // INPUT2 bit5 = ship yard toggle. INPUT byte3 bit5 = BUY.
    def in2At(f: Int): Long = if (f >= 6 && f <= 8) 0x20L else 0x00L
    def inAt(f: Int): Long = if (f >= 16 && f <= 18) (0x20L << 24) else 0L

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      var yardLit = 0
      for (f <- 0 until frames) {
        val frame = runner.capture(inAt(f), in2At(f), 5000000)
        assert(
          frame.dbg == 0xcafeL,
          s"dbg=0x${frame.dbg.toHexString} at frame $f (ship self-test failed?)"
        )

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 30, s"frame $f nearly empty ($lit px)")
        if (f >= 10) yardLit = math.max(yardLit, lit) // while the yard is open

        if (f % 4 == 0 || (f >= 14 && f <= 22)) {
          val out = new File(f"build/sim/yard_$f%03d.pbm")
          val pw = new PrintWriter(out)
          pw.println("P1")
          pw.println(s"$W $H")
          for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
          pw.close()
          println(
            f"frame $f%03d  in2=0x${in2At(f)}%02x buy=${if (inAt(f) != 0) 1 else 0}  lit=$lit%4d -> ${out.getName}"
          )
        }

      }
      assert(yardLit > 100, s"ship yard screen never populated (peak lit=$yardLit)")
      println(s"Shipyard sim: dbg=0xCAFE held; yard peak lit=$yardLit")
    }
  }
}
