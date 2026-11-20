package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.soc.Soc
import org.scalatest.funsuite.AnyFunSuite

/** Stage A: boot the PicoRV32 SoC from build/fw/firmware.hex and confirm the CPU writes 0xCAFE to
  * the MMIO debug register. Proves CPU + RAM + bus + toolchain.
  *
  * Build firmware first: `make -C fw`. Run with `sbt "testOnly leet.sim.SocSim"`.
  */
class SocSim extends AnyFunSuite {
  test("CPU boots firmware and writes the debug register") {
    // picorv32 has many optional ports we leave unconnected; don't let Verilator
    // treat the resulting PINMISSING (and picorv32's own style warnings) as fatal.
    SimConfig.addSimulatorFlag("-Wno-fatal").compile(Soc()).doSim { dut =>
      dut.io.fbRdAddr #= 0
      dut.io.frameAck #= false
      dut.io.input #= 0
      dut.io.input2 #= 0
      dut.clockDomain.forkStimulus(period = 10)
      var c = 0
      while (dut.io.dbg.toLong != 0xcafeL && c < 200000) {
        dut.clockDomain.waitSampling()
        c += 1
      }
      assert(
        dut.io.dbg.toLong == 0xcafeL,
        s"dbg=0x${dut.io.dbg.toLong.toHexString} after $c cycles (trap=${dut.io.trap.toBoolean})"
      )
      println(s"CPU wrote 0x${dut.io.dbg.toLong.toHexString} to debug reg after $c cycles")
    }
  }
}
