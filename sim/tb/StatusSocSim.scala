package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase P-B commander status screen.
  *
  *   1. dbg == 0xCAFE — the boot sentinel now also folds in status_self_test (the legal-status band
  *      boundaries) plus the extended commander save (legal_status field) via cmdr_self_test, so a
  *      green boot validates both. 2. INPUT2 bit4 toggles the status screen, which prints
  *      "COMMANDER JAMESON" and the stat block — we watch the header ROI and require the screen to
  *      light it well above a quiescent flight frame.
  *
  * The button is held three frames and then released. The header sits at row 2; we watch rows
  * 2..10, cols 3..118.
  */
class StatusSocSim extends AnyFunSuite {
  test("boot self-test passes; INPUT2 bit4 opens the commander status screen", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 30

    def in2At(f: Int): Long = if (f >= 8 && f <= 10) 0x10L else 0x00L // bit4 = status

    val roiY = 2 until 11
    val roiX = 3 until 118
    def roiLit(fb: Array[Int]): Int =
      roiY.map(y => roiX.count(x => fb(y * W + x) != 0)).sum

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      var baseRoi = 1 << 30
      var statusRoi = 0

      for (f <- 0 until frames) {
        val frame = runner.capture(0, in2At(f), 5000000)
        assert(
          frame.dbg == 0xcafeL,
          s"dbg=0x${frame.dbg.toHexString} at frame $f (status/legal self-test failed?)"
        )

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val roi = roiLit(fb)
        if (f < 8) baseRoi = math.min(baseRoi, roi)
        if (f >= 12) statusRoi = math.max(statusRoi, roi)

        val out = new File(f"build/sim/status_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%03d  in2=0x${in2At(f)}%02x  lit=$lit%4d  roi=$roi%3d -> ${out.getName}")

      }

      // The "COMMANDER JAMESON" header + stat block must light the top band well
      // above a quiescent flight frame (where it's just sparse stardust).
      assert(
        statusRoi >= baseRoi + 40,
        s"status screen header not seen (roi peak=$statusRoi, base=$baseRoi)"
      )
      println(s"Status sim: dbg=0xCAFE held; baseRoi=$baseRoi statusRoi=$statusRoi")
    }
  }
}
