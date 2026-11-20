package leet.bbc

import spinal.core._

/** MC6850 and serial ULA for the declared fixture with no tape or RS-423 peer. Register writes,
  * transmit completion, CTS selection and transmit IRQs remain digital device behavior even without
  * an attached peer. Baud deadlines follow the original 2 MHz scheduler profile.
  */
case class BbcSerial() extends Component {
  val io = new Bundle {
    val tick = in Bool () // 2 MHz, including CPU slots stretched by slow accesses
    val aciaRead = in Bool ()
    val aciaWrite = in Bool ()
    val serialRead = in Bool ()
    val serialWrite = in Bool ()
    val address = in Bool ()
    val data = in Bits (8 bits)
    val result = out Bits (8 bits)
    val irq = out Bool ()
  }
  val control = Reg(Bits(8 bits)) init (0)
  val baud = Reg(UInt(3 bits)) init (0)
  val rs423 = RegInit(False)
  val empty = RegInit(True)
  val pending = RegInit(False)
  // The longest byte deadline is 293333 ticks (146.6665 ms at 2 MHz).
  val deadlineWidth = 19
  val remaining = Reg(UInt(deadlineWidth bits)) init (0)
  val transmitPeriod = Reg(UInt(deadlineWidth bits)) init (1145)

  def bytePeriod(command: Bits, rate: UInt): UInt = {
    val format = command(2, 3 bits)
    val tenBits = format === B(2, 3 bits) || format === B(3, 3 bits) || format === B(5, 3 bits)
    // Complete byte deadlines in 2 MHz ticks, indexed by the serial ULA baud code.
    val ten = Vec(
      Seq(1041, 2083, 4166, 8333, 16666, 66666, 133333, 266666).map(U(_, deadlineWidth bits))
    )
    val eleven = Vec(
      Seq(1145, 2291, 4583, 9166, 18333, 73333, 146666, 293333).map(U(_, deadlineWidth bits))
    )
    Mux(tenBits, ten(rate), eleven(rate))
  }

  // Completion is applied before bus side effects on the same endpoint;
  // a data write below takes priority and starts a new byte.
  val finishing = io.tick && pending && remaining === 1
  when(io.tick && pending) {
    remaining := remaining - 1
    when(finishing) { pending := False; empty := True }
  }
  val serialTouch = io.serialRead || io.serialWrite
  val serialValue = Mux(io.serialRead, B(0xfe, 8 bits), io.data)
  val controlWrite = io.aciaWrite && !io.address
  val masterReset = controlWrite && io.data(0, 2 bits) === B(3, 2 bits)
  val dataWrite = io.aciaWrite && io.address
  val updateControl = controlWrite && !masterReset
  when(serialTouch) {
    baud := serialValue(0, 3 bits).asUInt
    rs423 := serialValue(6)
  }
  when(masterReset) {
    // Reset retains CR and a scheduled transmit completion.
    empty := True
  }
  when(updateControl) { control := io.data }

  // Share the lookup between control and baud writes. A coincident control
  // write wins and uses the old baud latch, matching the bus update order.
  val periodControl = Mux(updateControl, io.data, control)
  val periodBaud = Mux(updateControl, baud, serialValue(0, 3 bits).asUInt)
  when(serialTouch || updateControl) {
    transmitPeriod := bytePeriod(periodControl, periodBaud)
  }
  when(dataWrite) {
    empty := False
    pending := True
    remaining := transmitPeriod
  }
  // The independent scheduler completes a byte before the same bus endpoint's
  // read or write. Forward that result and the accepted write's IRQ effect.
  val visibleEmpty = empty || finishing
  val txIrq = !rs423 && visibleEmpty && control(5, 2 bits) === B(1, 2 bits)
  io.result := B(0, 8 bits)
  when(!io.address) {
    io.result(1) := visibleEmpty && !rs423
    io.result(3) := rs423
    io.result(7) := txIrq
  }
  val nextControl = Mux(controlWrite && !masterReset, io.data, control)
  val nextRs423 = Mux(serialTouch, serialValue(6), rs423)
  val nextEmpty = Mux(dataWrite, False, visibleEmpty || masterReset)
  io.irq := !nextRs423 && nextEmpty && nextControl(5, 2 bits) === B(1, 2 bits)
}
