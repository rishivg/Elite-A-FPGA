package leet.soc

import spinal.core._

/** Hold button press edges until an MMIO read consumes them. Firmware may draw for longer than a
  * keypress; axes remain current held values, while a short button press must survive rendering.
  * Multiple presses before a read coalesce.
  */
case class InputCapture() extends Component {
  val io = new Bundle {
    val input = in Bits (32 bits)
    val input2 = in Bits (32 bits)
    val consume = in Bool ()
    val consume2 = in Bool ()
    val word = out Bits (32 bits)
    val word2 = out Bits (32 bits)
  }
  val sampled = RegNext(io.input) init (0)
  val sampled2 = RegNext(io.input2) init (0)
  val previous = RegNext(sampled(31 downto 24)) init (0)
  val previous2 = RegNext(sampled2) init (0)
  val rising = sampled(31 downto 24) & ~previous
  val rising2 = sampled2 & ~previous2
  val pending = Reg(Bits(8 bits)) init (0)
  val pending2 = Reg(Bits(32 bits)) init (0)
  pending := pending | rising
  pending2 := pending2 | rising2
  // The current rising edge is also returned combinationally in this read.
  // Clearing on the actual bus handshake avoids removing it before the CPU
  // sees mem_ready, one cycle after the peripheral's request phase.
  when(io.consume) { pending := 0 }
  when(io.consume2) { pending2 := 0 }
  io.word := (sampled(31 downto 24) | pending | rising) ## sampled(23 downto 0)
  io.word2 := sampled2 | pending2 | rising2
}
