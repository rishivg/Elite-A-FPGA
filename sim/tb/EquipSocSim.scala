package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Phase 8.2 checkpoint: the Equip Ship outfitter.
  *
  * Scripted scenario: cruise, open equipment, select missiles, purchase twice, try an unaffordable
  * cargo upgrade, then close equipment. Each button press is held for three frames and released
  * between actions; InputCaptureSim checks shorter presses separately.
  *
  * The purchase gates + effects are proven bit-exact by tools/oracle/equip.py; this sim proves the
  * firmware drives the outfitter live on the SoC without trapping and renders a populated screen.
  *
  * Input byte 3 bits: bit0 fire, bit1 chart, bit2 hyper, bit3 dockit, bit4 market, bit5
  * buy/purchase, bit6 sell, bit7 equip.
  */
class EquipSocSim extends AnyFunSuite {
  test("open equip -> select -> purchase -> close", DemoFrames) {
    val W = 128
    val H = 128
    val frames = 58

    /** (firstFrame, roll, pitch, speed, btn3). */
    val schedule = Vector[(Int, Int, Int, Int, Int)](
      (0, 0, 0, 0, 0x00),
      (7, 0, 0, 0, 0x80), // equip toggle ON (held 3)
      (10, 0, 0, 0, 0x00),
      (12, 0, 1, 0, 0x00), // pitch down: select MISSL
      (20, 0, 0, 0, 0x20), // PURCHASE (held 3) -> missile
      (23, 0, 0, 0, 0x00),
      (26, 0, 0, 0, 0x20), // PURCHASE again
      (29, 0, 0, 0, 0x00),
      (32, 0, 1, 0, 0x00), // select CARGO (unaffordable)
      (40, 0, 0, 0, 0x20), // PURCHASE attempt (rejected: too poor)
      (43, 0, 0, 0, 0x00),
      (50, 0, 0, 0, 0x80), // equip toggle OFF
      (53, 0, 0, 0, 0x00)
    )
    // Long math: bit 7 of byte 3 (the equip toggle) lands at bit 31, which
    // overflows a signed Int — keep the whole word in a Long and mask to 32 bits.
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

        val out = new File(f"build/sim/equip_$f%03d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        val b3 = (stickAt(f) >> 24) & 0xff
        println(f"frame $f%03d  btn=0x$b3%02x  lit=$lit%4d  -> ${out.getName}")

      }
      println(s"Equip sim: $frames frames captured")
    }
  }
}
