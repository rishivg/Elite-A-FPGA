package leet.render.elite

import spinal.core._

/** CPU interface to the authentic LL9 face stage. Overflow-modified dots are carried to subsequent
  * faces; XX17's original scale and sign bits stay put. Full 32-bit writes only. An attempted start
  * while busy is ignored.
  */
case class EliteFaceRegisters() extends Component {
  val io = new Bundle {
    val write = in Bool ()
    val address = in UInt (3 bits)
    val data = in Bits (32 bits)
    val read = out Bits (32 bits)
    val reserved = in Bool ()
    val loadDots = in Bool ()
    val loadedDots = in Bits (24 bits)
    val loadedSigns = in Bits (3 bits)
    val busy = out Bool ()
  }
  val normal = Reg(Bits(32 bits)) init (0)
  val dots = Vec(Reg(UInt(8 bits)) init (0), 3)
  val settings = Reg(Bits(32 bits)) init (0)
  val done = RegInit(False)
  val kernel = EliteFaceVisibility()
  val start =
    io.write && io.address === 3 && io.data(0) && !kernel.io.busy && !io.reserved && !io.loadDots
  io.busy := kernel.io.busy
  kernel.io.start := start
  kernel.io.normal := normal
  kernel.io.dotMagnitude := dots
  kernel.io.dotSign := settings(2 downto 0)
  kernel.io.scale := settings(15 downto 8).asUInt
  kernel.io.distance := settings(20 downto 16).asUInt
  when(start) { done := False }
  when(kernel.io.valid) {
    done := True
    dots := kernel.io.nextDotMagnitude
  }
  // Hold operands while the kernel is busy, including its result edge.
  when(io.loadDots && !kernel.io.busy) {
    for (i <- 0 until 3) dots(i) := io.loadedDots(8 * i, 8 bits).asUInt
    settings(2 downto 0) := io.loadedSigns
    done := False
  }
  when(io.write && !kernel.io.busy && !io.reserved && !io.loadDots) {
    switch(io.address) {
      is(0) { normal := io.data }
      is(1) { for (i <- 0 until 3) dots(i) := io.data(8 * i, 8 bits).asUInt }
      is(2) { settings := io.data }
    }
  }
  io.read := 0
  switch(io.address) {
    is(0) { io.read := normal }
    is(1) { for (i <- 0 until 3) io.read(8 * i, 8 bits) := dots(i).asBits }
    is(2) { io.read := settings }
    is(3) {
      io.read(0) := kernel.io.busy
      io.read(1) := done
    }
    is(4) { io.read := kernel.io.visibility.asBits.resize(32) }
  }
}
