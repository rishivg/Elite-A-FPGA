package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common._
import leet.render.Transform
import org.scalatest.funsuite.AnyFunSuite

class TransformSim extends AnyFunSuite {
  test("identity matrix passes the vertex through unchanged") {
    SimConfig.withWave.compile(Transform()).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)

      val ident = Seq(1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0)
      def fx(d: Double): Long = math.round(d * (1 << Fixed.FRAC))
      ident.zipWithIndex.foreach { case (v, i) => dut.io.m(i) #= fx(v) }
      dut.io.t.x #= 0
      dut.io.t.y #= 0
      dut.io.t.z #= 0

      dut.io.vIn.valid #= true
      dut.io.vIn.payload.x #= fx(3.0)
      dut.io.vIn.payload.y #= fx(-2.0)
      dut.io.vIn.payload.z #= fx(5.0)
      dut.clockDomain.waitSampling()
      dut.io.vIn.valid #= false
      dut.clockDomain.waitSampling()

      assert(dut.io.vOut.valid.toBoolean)
      assert(dut.io.vOut.payload.x.toLong == fx(3.0))
      assert(dut.io.vOut.payload.y.toLong == fx(-2.0))
      assert(dut.io.vOut.payload.z.toLong == fx(5.0))
    }
  }
}
