package leet.sim

import spinal.core.sim._
import leet.bbc.BbcRam
import org.scalatest.funsuite.AnyFunSuite

class BbcRamSim extends AnyFunSuite {
  test("CPU data survives intervening video reads and shares writes with scanout") {
    SimConfig.compile(BbcRam()).doSim { dut =>
      SimTimeout(300000)
      dut.clockDomain.forkStimulus(10)
      dut.io.cpuEnable #= false
      dut.io.videoEnable #= false
      dut.io.cpuWrite #= false
      dut.io.cpuAddress #= 0
      dut.io.videoAddress #= 0
      dut.io.cpuData #= 0

      dut.io.debugAddress #= 0
      dut.clockDomain.waitSampling(3)
      def step(): Unit = {
        dut.clockDomain.waitSampling()
        sleep(1)
      }
      val addresses = (0 until 512).map(i => (i * 7919) & 32767)
      for ((address, i) <- addresses.zipWithIndex) {
        dut.io.cpuAddress #= address
        dut.io.cpuData #= (i & 255)
        dut.io.cpuEnable #= true
        dut.io.cpuWrite #= true
        step()
        dut.io.cpuEnable #= false
        dut.io.cpuWrite #= false
        step()
      }
      for ((address, i) <- addresses.zipWithIndex) {
        dut.io.cpuAddress #= address
        dut.io.cpuEnable #= true
        step()
        if (i > 0)
          assert(
            dut.io.videoReadData.toInt == (i & 255),
            "CPU reads must not replace the ULA's current byte"
          )
        dut.io.cpuEnable #= false
        dut.io.videoEnable #= true
        dut.io.videoAddress #= addresses((i + 1) % 512)
        step()
        assert(dut.io.cpuReadData.toInt == (i & 255))
        assert(dut.io.videoReadData.toInt == ((i + 1) & 255))
        dut.io.videoEnable #= false
        for (_ <- 0 until 5) {
          step()
          assert(dut.io.cpuReadData.toInt == (i & 255))
          assert(dut.io.videoReadData.toInt == ((i + 1) & 255))
        }
        dut.io.debugAddress #= address
        sleep(1)
        assert(dut.io.debugData.toInt == (i & 255))
      }
    }
  }
}
