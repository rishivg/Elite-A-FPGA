package leet.sim

import spinal.core.sim._
import leet.soc.Soc
import org.scalatest.funsuite.AnyFunSuite

/** Actual PicoRV32 firmware writes the public MMIO registers and compares results against
  * released-game expectations; no direct internal bus forcing.
  */
class SocEliteRendererSim extends AnyFunSuite {
  test(
    "PicoRV32 reaches original orientation, face and screen-vertex pipelines through MMIO",
    RenderKernel
  ) {
    val image = sys.env.getOrElse(
      "ELITE_FACE_FIRMWARE",
      throw new IllegalArgumentException(
        "Run make validation-render to build verified MMIO fixture firmware"
      )
    )
    val compiled = SimConfig.compile(Soc(firmwareHex = image))
    // Retain the seed that exposed observing uninitialized debug scratch at time zero.
    for (seed <- Seq(0, 1056996955)) {
      compiled.doSim(seed = seed) { dut =>
        SimTimeout(30000000)
        dut.clockDomain.forkStimulus(10)
        dut.io.input #= 0
        dut.io.input2 #= 0
        dut.io.fbRdAddr #= 0
        dut.io.frameAck #= false
        // Clock the testbench reset and the SoC's 64-cycle POR before reading scratch.
        dut.clockDomain.waitSampling(128)
        var guard = 0
        while (dut.io.dbg.toLong == 0 && guard < 2000000) {
          dut.clockDomain.waitSampling(20)
          guard += 20
          assert(!dut.io.trap.toBoolean, "MMIO validation firmware trapped")
        }
        assert(guard < 2000000, "MMIO fixture firmware did not complete")
        assert(
          dut.io.dbg.toLong == 0xe11ea001L,
          f"MMIO validation failed: 0x${dut.io.dbg.toLong}%08x"
        )
      }
    }
  }
}
