package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.video.FramePresenter
import org.scalatest.funsuite.AnyFunSuite

class FramePresenterSim extends AnyFunSuite {
  test("held frames copy completely, swap only in blanking, and never tear") {
    val compiled = SimConfig.compile(new Component {
      val io = new Bundle {
        val ready = in Bool ()
        val blankStart = in Bool ()
        val sourceWrite = in Bool ()
        val sourceAddr = in UInt (5 bits)
        val sourceValue = in UInt (4 bits)
        val scanAddr = in UInt (5 bits)
        val pixel = out UInt (4 bits)
        val ack = out Bool ()
        val valid = out Bool ()
      }
      val source = Mem(UInt(4 bits), 32)
      source.write(io.sourceAddr, io.sourceValue, enable = io.sourceWrite)
      val presenter = FramePresenter(32, 4)
      presenter.io.sourceReady := io.ready
      presenter.io.blankStart := io.blankStart
      presenter.io.sourceData := source.readSync(presenter.io.sourceAddr)
      presenter.io.scanAddr := io.scanAddr
      io.pixel := Mux(presenter.io.valid, presenter.io.scanData, U(0, 4 bits))
      io.ack := presenter.io.sourceAck
      io.valid := presenter.io.valid
    })
    compiled.doSim { dut =>
      SimTimeout(50000)
      dut.clockDomain.forkStimulus(10)
      dut.io.ready #= false
      dut.io.blankStart #= false
      dut.io.sourceWrite #= false
      dut.io.sourceAddr #= 0
      dut.io.sourceValue #= 0
      dut.io.scanAddr #= 0
      dut.clockDomain.waitSampling(5)
      sleep(1)
      assert(!dut.io.valid.toBoolean && dut.io.pixel.toInt == 0)
      def fill(offset: Int): Unit = {
        for (a <- 0 until 32) {
          dut.io.sourceWrite #= true
          dut.io.sourceAddr #= a

          dut.io.sourceValue #= (a * 7 + offset) % 16
          dut.clockDomain.waitSampling()
          sleep(1)
        }
        dut.io.sourceWrite #= false
      }
      def scan(offset: Int): Unit = {
        for (a <- 0 until 32) {
          dut.io.scanAddr #= a
          dut.clockDomain.waitSampling(2)
          sleep(1)
          assert(
            dut.io.pixel.toInt == (a * 7 + offset) % 16,
            s"scan address $a, offset $offset: ${dut.io.pixel.toInt}"
          )
        }
      }
      def swap(): Unit = {
        dut.io.blankStart #= true
        sleep(1)
        assert(dut.io.ack.toBoolean, "complete back bank was not acknowledged in blanking")
        dut.clockDomain.waitSampling()
        sleep(1)
        dut.io.blankStart #= false
        dut.io.ready #= false
        dut.clockDomain.waitSampling(2)
        sleep(1)
        assert(dut.io.valid.toBoolean && !dut.io.ack.toBoolean)
      }
      fill(0)
      dut.io.ready #= true
      // A blanking pulse during the copy must not publish a partial frame.
      dut.clockDomain.waitSampling(8)
      dut.io.blankStart #= true
      sleep(1)
      assert(!dut.io.ack.toBoolean)
      dut.clockDomain.waitSampling()
      dut.io.blankStart #= false
      dut.clockDomain.waitSampling(40)
      sleep(1)
      assert(!dut.io.valid.toBoolean && !dut.io.ack.toBoolean)
      swap()
      scan(0)
      fill(3)
      dut.io.ready #= true
      // Scan the original bank while the replacement is being copied.
      scan(0)
      assert(!dut.io.ack.toBoolean)
      swap()
      scan(3)
      fill(9)
      dut.io.ready #= true
      dut.clockDomain.waitSampling(40)
      swap()
      scan(9)
      dut.clockDomain.assertReset()
      sleep(31)
      assert(!dut.io.valid.toBoolean && !dut.io.ack.toBoolean && dut.io.pixel.toInt == 0)
    }
  }
}
