package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

class SaveSocSim extends AnyFunSuite {
  test("boot self-test passes; save + load buttons raise the overlay", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 40

    // INPUT2 schedule: bit2 = save, bit3 = load. Hold each 3 frames.
    def in2At(f: Int): Long =
      if (f >= 8 && f <= 10) 0x04L // SAVE
      else if (f >= 24 && f <= 26) 0x08L // LOAD
      else 0x00L

    // Overlay ROI: the SAVED/LOADED text box, kept clear of the dashboard band.
    val roiY = 8 until 16
    val roiX = 49 until 86
    def roiLit(fb: Array[Int]): Int =
      roiY.map(y => roiX.count(x => fb(y * W + x) != 0)).sum

    SocFrames.run { runner =>
      new File("build/sim").mkdirs()
      var baseRoi = 1 << 30 // quiescent ROI (a pre-save frame): overlay floor
      var saveRoi = 0 // peak ROI while SAVED should be up
      var loadRoi = 0 // peak ROI while LOADED should be up

      for (f <- 0 until frames) {
        val frame = runner.capture(0, in2At(f), 5000000)
        // The boot self-test must have set DBG=0xCAFE (galaxy AND commander OK).
        assert(
          frame.dbg == 0xcafeL,
          s"dbg=0x${frame.dbg.toHexString} at frame $f (commander self-test failed?)"
        )

        val fb = frame.pixels
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val roi = roiLit(fb)
        if (f < 8) baseRoi = math.min(baseRoi, roi) // before any save
        if (f >= 9 && f <= 18) saveRoi = math.max(saveRoi, roi) // SAVED window
        if (f >= 25 && f <= 34) loadRoi = math.max(loadRoi, roi) // LOADED window

        val out = new File(f"build/sim/save_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%03d  in2=0x${in2At(f)}%02x  lit=$lit%4d  roi=$roi%3d -> ${out.getName}")

      }

      // The SAVED and LOADED overlays must each light their text box well above
      // the quiescent baseline (each is multiple 8x8 glyphs).
      assert(saveRoi >= baseRoi + 20, s"SAVED overlay not seen (roi peak=$saveRoi, base=$baseRoi)")
      assert(loadRoi >= baseRoi + 20, s"LOADED overlay not seen (roi peak=$loadRoi, base=$baseRoi)")
      println(s"Save sim: dbg=0xCAFE held; baseRoi=$baseRoi saveRoi=$saveRoi loadRoi=$loadRoi")
    }
  }
}
