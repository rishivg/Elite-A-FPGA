package leet.sim

import spinal.core.sim._
import leet.render.elite.EliteFaceVisibility
import org.scalatest.funsuite.AnyFunSuite

/** Expected bytes come from executing released 1.F in pinned jsbeeb, including natural unmodified
  * game observations and injected kernel boundary cases. Run through make validation-render to
  * produce and select verified fixtures.
  */
class EliteFaceVisibilitySim extends AnyFunSuite {
  test("face bytes and overflow side effects match the released 6502 routine", RenderKernel) {
    val cases = EliteFixture.load("ELITE_FACE_CASES", 14, 10000)
    assert(cases.size >= 10000, "Renderer oracle coverage is incomplete")
    assert(cases.forall(c => c.length == 14 && c.forall(v => v >= 0 && v <= 255)))
    SimConfig.compile(EliteFaceVisibility()).doSim { dut =>
      SimTimeout(cases.size.toLong * 300)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.normal #= 0
      dut.io.dotSign #= 0
      dut.io.scale #= 0

      dut.io.distance #= 0
      for (i <- 0 until 3) dut.io.dotMagnitude(i) #= 0
      dut.clockDomain.waitSampling(3)
      for ((c, index) <- cases.zipWithIndex) {
        dut.io.normal #= c.take(4).zipWithIndex.map { case (v, i) => v.toLong << (8 * i) }.sum
        for (i <- 0 until 3) dut.io.dotMagnitude(i) #= c(4 + i)
        dut.io.dotSign #= c(7)
        dut.io.scale #= c(8)
        dut.io.distance #= c(9)
        assert(!dut.io.busy.toBoolean)
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        // A request owns its operands after the acceptance edge.
        dut.io.normal #= 0
        dut.io.dotSign #= 0
        dut.io.scale #= 255
        dut.io.distance #= 31
        for (i <- 0 until 3) dut.io.dotMagnitude(i) #= 0
        var count = 0
        while (!dut.io.valid.toBoolean && count < 20) {
          dut.clockDomain.waitSampling()
          count += 1
        }
        assert(count < 20, s"face kernel stuck, case $index")
        val actual =
          Seq(dut.io.visibility.toInt) ++ (0 until 3).map(i => dut.io.nextDotMagnitude(i).toInt)
        assert(
          actual == c.drop(10).toSeq,
          s"case $index ${c.mkString(",")}: got ${actual.mkString(",")}"
        )
        dut.clockDomain.waitSampling(2)
        assert(!dut.io.valid.toBoolean)
      }
      println(s"Released Elite-A: ${cases.size} face cases matched byte for byte")
    }
  }
}
