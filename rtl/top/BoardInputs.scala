package leet.top

import spinal.core._
import spinal.lib._

/** Synchronize and debounce the complete control vector before encoding MMIO. Axes: left/right,
  * pitch-up/down, throttle-up/down. Opposite directions cancel. Buttons use the firmware's INPUT
  * byte 3 and low INPUT2 bits respectively.
  */
case class BoardInputs(debounceCycles: Int) extends Component {
  require(debounceCycles >= 2)
  val io = new Bundle {
    val axes = in Bits (6 bits)
    val buttons = in Bits (8 bits)
    val buttons2 = in Bits (6 bits)
    val input = out Bits (32 bits)
    val input2 = out Bits (32 bits)
  }
  val sampled = BufferCC(io.buttons2 ## io.buttons ## io.axes, init = B(0, 20 bits))
  val candidate = Reg(Bits(20 bits)) init (0)
  val stable = Reg(Bits(20 bits)) init (0)
  val count = Reg(UInt(log2Up(debounceCycles) bits)) init (0)
  when(sampled =/= candidate) {
    candidate := sampled
    count := 0
  } otherwise {
    when(count =/= debounceCycles - 1) { count := count + 1 }
    when(count === debounceCycles - 1) { stable := candidate }
  }
  def axis(positive: Bool, negative: Bool): Bits =
    Mux(positive ^ negative, Mux(positive, B(1, 8 bits), B(255, 8 bits)), B(0, 8 bits))
  io.input := stable(13 downto 6) ## axis(stable(4), stable(5)) ##
    axis(stable(2), stable(3)) ## axis(stable(1), stable(0))
  io.input2 := stable(19 downto 14).resize(32)
}
