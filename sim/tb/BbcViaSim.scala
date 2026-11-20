package leet.sim

import spinal.core.sim._
import leet.bbc.Via6522
import org.scalatest.funsuite.AnyFunSuite

class BbcViaSim extends AnyFunSuite {
  test("MOS port reads and CA1/CB1 input latches capture each active edge") {
    SimConfig.compile(Via6522()).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.tick #= false
      dut.io.phi2 #= false
      dut.io.read #= false
      dut.io.write #= false
      dut.io.address #= 0
      dut.io.data #= 0
      dut.io.portAIn #= 255
      dut.io.portBIn #= 255
      dut.io.ca1 #= false
      dut.io.ca2 #= false
      dut.io.ca2AfterWrite #= false
      dut.io.cb1 #= false
      dut.io.cb1Pulse #= false

      dut.io.cb2 #= false
      dut.clockDomain.waitSampling(3)
      def step(): Unit = {
        dut.clockDomain.waitSampling()
        sleep(1)
      }
      def write(a: Int, d: Int): Unit = {
        dut.io.address #= a
        dut.io.data #= d
        dut.io.write #= true
        step()
        dut.io.write #= false
      }
      def peek(a: Int): Int = {
        dut.io.address #= a
        sleep(1)
        dut.io.result.toInt
      }
      def read(a: Int): Int = {
        val value = peek(a)
        dut.io.read #= true
        step()
        dut.io.read #= false
        value
      }
      write(2, 255)
      write(0, 0xa5)
      dut.io.portBIn #= 0
      step()
      assert(
        dut.io.portB.toInt == 0 && read(0) == 0xa5,
        "PB outputs read ORB despite a low external pin"
      )
      write(3, 255)
      write(1, 0xff)
      dut.io.portAIn #= 0x12
      step()
      assert(read(15) == 0x12, "PA output reads actual pin levels")
      write(2, 0xf0)
      write(0, 0xa5)
      dut.io.portBIn #= 0x0b
      step()
      assert(read(0) == 0xab, "PB merges ORB output bits with physical input bits")
      write(3, 0)
      write(12, 0x11)
      write(11, 3)
      write(13, 0x7f)
      dut.io.portAIn #= 0x34
      dut.io.portBIn #= 0x05
      step()
      assert(peek(15) == 0 && peek(0) == 0xa0, "enabling a latch preserves its previous capture")
      dut.io.ca1 #= true
      dut.io.cb1 #= true
      step()
      dut.io.portAIn #= 0x56
      dut.io.portBIn #= 0x0c
      step()
      assert(peek(15) == 0x34 && peek(0) == 0xa5)
      dut.io.ca1 #= false
      dut.io.cb1 #= false
      dut.io.cb1Pulse #= false
      step()
      dut.io.ca1 #= true
      dut.io.cb1 #= true
      step()
      assert(
        peek(15) == 0x56 && peek(0) == 0xac,
        "each active edge refreshes the capture while IFR is set"
      )
      assert(read(15) == 0x56 && (peek(13) & 2) != 0, "ORA without handshake preserves CA1")
      assert(read(1) == 0x56 && peek(15) == 0x56, "ORA acknowledgement preserves the captured PA")
      assert(read(0) == 0xac && peek(0) == 0xac, "ORB acknowledgement preserves the captured PB")
      dut.io.ca1 #= false
      step()
      dut.io.ca1 #= true
      step()
      dut.io.portAIn #= 0x78
      step()
      assert(peek(15) == 0x56)
      write(13, 2)
      assert(peek(15) == 0x56, "explicit IFR clear preserves the input latch")
    }
  }
  test("VIA timers, PB7 and independently enabled edge interrupts") {
    SimConfig.compile(Via6522()).doSim { dut =>
      SimTimeout(100000)
      dut.clockDomain.forkStimulus(10)
      dut.io.tick #= false
      dut.io.phi2 #= false
      dut.io.read #= false
      dut.io.write #= false
      dut.io.address #= 0
      dut.io.data #= 0
      dut.io.portAIn #= 255
      dut.io.portBIn #= 255
      dut.io.ca1 #= false
      dut.io.ca2 #= false
      dut.io.ca2AfterWrite #= false
      dut.io.cb1 #= false
      dut.io.cb1Pulse #= false

      dut.io.cb2 #= false
      dut.clockDomain.waitSampling(3)
      // Settle the deasserted enable before observing the post-edge state;
      // while tick is high the bus bypass describes the next enabled edge.
      def step(tick: Boolean = false): Unit = {
        dut.io.tick #= tick
        dut.io.phi2 #= tick
        dut.clockDomain.waitSampling()
        sleep(1)
        dut.io.tick #= false

        sleep(1)
      }
      def write(a: Int, d: Int): Unit = {
        dut.io.address #= a
        dut.io.data #= d
        dut.io.write #= true
        step()
        dut.io.write #= false
      }
      def read(a: Int): Int = {
        dut.io.address #= a
        dut.io.read #= true
        sleep(1)
        val d = dut.io.result.toInt
        step()

        dut.io.read #= false
        d
      }
      write(14, 0xc0)
      write(11, 0x80)
      write(4, 2)
      write(5, 0)
      assert(!dut.io.irq.toBoolean && (dut.io.portB.toInt & 128) == 0)
      for (_ <- 0 until 3) {
        step(true)
        assert(!dut.io.irq.toBoolean)
      }
      step(true)
      assert(dut.io.irq.toBoolean && (dut.io.portB.toInt & 128) != 0)
      assert((read(13) & 0xc0) == 0xc0)
      read(4)
      assert(!dut.io.irq.toBoolean)
      for (_ <- 0 until 12) step(true)
      assert(!dut.io.irq.toBoolean, "one-shot must not interrupt repeatedly")
      write(11, 0xc0)
      write(5, 0)
      for (_ <- 0 until 4) step(true)
      assert(dut.io.irq.toBoolean)
      read(4)
      for (_ <- 0 until 3) {
        step(true)
        assert(!dut.io.irq.toBoolean)
      }
      step(true)
      assert(dut.io.irq.toBoolean)
      write(14, 0x40)
      read(4)
      write(14, 0xa0)
      write(11, 0)
      write(8, 0)
      write(9, 0)
      step(true)
      assert(!dut.io.irq.toBoolean)
      step(true)
      assert(dut.io.irq.toBoolean)
      read(8)
      write(11, 0x20)
      write(8, 1)
      write(9, 0)
      for (_ <- 0 until 4) step(true)
      assert(!dut.io.irq.toBoolean, "pulse mode must ignore the clock enable")
      // PB6 settles in PHI1, is accepted in PHI2, then counts in PHI1.
      def pulseCycle(): Unit = { step(); step(true); step() }
      dut.io.portBIn #= 191
      pulseCycle()
      assert(!dut.io.irq.toBoolean)
      dut.io.portBIn #= 255
      pulseCycle()
      dut.io.portBIn #= 191
      pulseCycle()
      assert(dut.io.irq.toBoolean, "N=1 must expire after two falling PB6 edges")
      read(8)
      write(14, 0x20)
      write(14, 0x82)
      write(12, 1)
      dut.io.ca1 #= true
      step()
      assert(dut.io.irq.toBoolean && (read(13) & 2) == 2)
      read(15)
      assert(dut.io.irq.toBoolean, "ORA without handshake must preserve CA1")
      read(1)
      assert(!dut.io.irq.toBoolean)
      write(14, 0x80)
      assert(read(14) == 0x82)
      dut.clockDomain.assertReset()
      sleep(30)
      assert(!dut.io.irq.toBoolean)
    }
  }
}
