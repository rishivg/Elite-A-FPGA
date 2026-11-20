package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.render.Divider
import org.scalatest.funsuite.AnyFunSuite

/** Checks the sequential divider against Scala integer division over a spread of operands. Run with
  * `sbt "testOnly leet.sim.DividerSim"`.
  */
class DividerSim extends AnyFunSuite {
  test("quotient and remainder match integer division") {
    SimConfig.compile(Divider(nW = 26, dW = 16)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      dut.io.start #= false
      dut.io.numer #= 0
      dut.io.denom #= 1
      dut.clockDomain.waitSampling(3)

      def divide(n: Long, d: Long): (Long, Long) = {
        dut.io.numer #= n
        dut.io.denom #= d
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 100) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 100, s"divider did not finish for $n/$d")
        (dut.io.quotient.toLong, dut.io.remainder.toLong)
      }

      val cases = Seq(
        (1000L, 7L),
        (65535L, 256L),
        (0L, 5L),
        (51200L, 1280L),
        (123456L, 999L),
        (7L, 8L),
        (1L, 1L)
      )
      for ((n, d) <- cases) {
        val (q, r) = divide(n, d)
        assert(q == n / d, s"$n/$d: quotient $q != ${n / d}")
        assert(r == n % d, s"$n/$d: remainder $r != ${n % d}")
      }
      println(s"divider: ${cases.size} cases OK")
    }
  }
}
