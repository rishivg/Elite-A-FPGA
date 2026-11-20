package leet.soc

import spinal.core._

/** SpinalHDL BlackBox for the PicoRV32 RISC-V core (third_party/picorv32.v).
  *
  * Only the native memory interface is exposed; the look-ahead, PCPI, IRQ-detail and RISCV_FORMAL
  * ports are left unconnected (outputs) or tied off (inputs). Clock/reset are wired by the parent
  * SoC. RV32IM (hardware mul/div) + barrel shifter so firmware can use `mul` and single-cycle
  * shifts.
  */
class PicoRV32(progReset: BigInt = 0) extends BlackBox {
  val io = new Bundle {
    val clk = in Bool ()
    val resetn = in Bool ()
    val trap = out Bool ()
    val mem_valid = out Bool ()
    val mem_instr = out Bool ()
    val mem_ready = in Bool ()
    val mem_addr = out UInt (32 bits)
    val mem_wdata = out Bits (32 bits)
    val mem_wstrb = out Bits (4 bits)
    val mem_rdata = in Bits (32 bits)
    // Declare optional outputs too, so generated instances explicitly connect
    // or prune them rather than silently omitting ports from the black box.
    val mem_la_read = out Bool ()
    val mem_la_write = out Bool ()
    val mem_la_addr = out UInt (32 bits)
    val mem_la_wdata = out Bits (32 bits)
    val mem_la_wstrb = out Bits (4 bits)
    val pcpi_valid = out Bool ()
    val pcpi_insn = out Bits (32 bits)
    val pcpi_rs1 = out Bits (32 bits)
    val pcpi_rs2 = out Bits (32 bits)
    val eoi = out Bits (32 bits)
    val trace_valid = out Bool ()
    val trace_data = out Bits (36 bits)
    val irq = in Bits (32 bits)
    val pcpi_wr = in Bool ()
    val pcpi_rd = in Bits (32 bits)
    val pcpi_wait = in Bool ()
    val pcpi_ready = in Bool ()
  }
  setDefinitionName("picorv32")
  noIoPrefix()

  addGeneric("ENABLE_MUL", 1)
  addGeneric("ENABLE_DIV", 1)
  addGeneric("BARREL_SHIFTER", 1)
  addGeneric("COMPRESSED_ISA", 0)
  addGeneric("ENABLE_IRQ", 0)
  addGeneric("PROGADDR_RESET", progReset)

  addRTLPath("third_party/picorv32.v")
}
