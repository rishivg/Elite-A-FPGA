package leet.sim

import org.scalatest.funsuite.AnyFunSuite
import scala.sys.process.Process

/** The native replay uses the actual generated 32 MHz RTL and independently executed Original
  * bus/flux histories. The former accelerated fixture supplied payload bytes directly, bypassing
  * headers, clocks and CRCs, and assumed a zeroed MMIO bank.
  */
class BbcDiscSim extends AnyFunSuite {
  test("Original READ/WRITE host accesses and physical FM payload writes") {
    assert(Process(Seq("python", "-m", "tools.bbc.disc_probe")).! == 0)
  }
}
