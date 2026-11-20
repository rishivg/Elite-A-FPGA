package leet.bbc

import spinal.core._

/** Synchronous-memory NMOS CPU with clock-enable and read-only diagnostics. */
case class Cpu6502() extends BlackBox {
  setDefinitionName("BbcCpuEvents")
  val io = new Bundle {
    val clk = in Bool ()
    val reset = in Bool ()
    val enable = in Bool ()
    val irq = in Bool ()
    val nmi = in Bool ()
    val nmi_event = in Bool ()
    val warm_reset = in Bool ()
    val read_data = in Bits (8 bits)
    val address = out UInt (16 bits)
    val write_data = out Bits (8 bits)
    val write_enable = out Bool ()
    val instruction = out Bool ()
    val sync = out Bool ()
    val pc = out UInt (16 bits)
    val accumulator = out Bits (8 bits)
    val index_x = out Bits (8 bits)
    val index_y = out Bits (8 bits)
    val stack_pointer = out Bits (8 bits)
    val flags = out Bits (8 bits)
  }
  noIoPrefix()
  addRTLPath("build/bbc/BbcCpuEvents.v")
  addRTLPath("build/bbc/ArletCpuEvents.v")
  addRTLPath("third_party/6502/ALU.v")
}
