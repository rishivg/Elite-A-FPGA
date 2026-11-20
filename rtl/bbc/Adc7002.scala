package leet.bbc

import spinal.core._

/** BBC uPD7002 register interface and conversion timing. Inputs are unsigned, left-aligned analogue
  * values: 0..FFFF is 0..Vref. The board supplies sampled values; this module models the digital
  * device, including its CB1 end-of-conversion signal, rather than an analogue front end.
  */
case class Adc7002() extends Component {
  val io = new Bundle {
    val tick = in Bool () // 1 MHz
    val read = in Bool ()
    val write = in Bool ()
    val address = in UInt (2 bits)
    val data = in Bits (8 bits)
    val channels = in Bits (64 bits)
    val result = out Bits (8 bits)
    val eocN = out Bool ()
    val eocPulse = out Bool () // completion followed by restart at the same bus edge
    val readRace = out Bool () // datasheet: reads coincident with completion are uncertain
    val eocBusN = out Bool () // completion visible at the current enabled bus edge
  }
  val control = Reg(Bits(4 bits)) init (0)
  val resultControl = Reg(Bits(4 bits)) init (0)
  val conversion = Reg(UInt(14 bits)) init (0)
  val busy = RegInit(False)
  val completed = RegInit(True)
  val sample = Reg(Bits(16 bits)) init (0)
  val values = Vec((0 until 4).map(i => io.channels(i * 16, 16 bits)))
  when(io.tick && busy) {
    conversion := conversion - 1
    when(conversion === 1) {
      busy := False
      completed := True
      resultControl := control
      sample := values(control(1 downto 0).asUInt)
      // Both modes expose the left-aligned 12-bit data register. The shorter
      // conversion specifies lower analogue accuracy, not an 8-bit bus mask.
      sample(3 downto 0) := 0
    }
  }
  // Starting another conversion cancels the old one; data registers retain
  // their last completed result until the new conversion finishes.
  when(io.write && io.address === 0) {
    control := io.data(3 downto 0)
    busy := True
    completed := False
    conversion := Mux(io.data(3), U(10000, 14 bits), U(4000, 14 bits))
  }
  // The NEC part has no power-on reset. This deterministic BBC fixture
  // follows the reference startup; it is not a claim about uninitialized silicon.
  // Original Model B reset exposes idle status 40 and a low CB1/EOC pin.
  // Starting a conversion raises EOC; completing it lowers EOC again.
  io.eocN := !completed
  val finishing = io.tick && busy && conversion === 1
  // Completion precedes a coincident control write. CB1 falls then rises even
  // though the final pin stays high; preserve both polarities for the VIA.
  val acknowledge = io.read && io.address === 1
  when(acknowledge) { completed := False }
  io.readRace := finishing && io.read
  io.eocPulse := finishing && ((io.write && io.address === 0) || acknowledge)
  io.eocBusN := !completed && !finishing
  when((io.write && io.address === 0) || acknowledge) { io.eocBusN := True }
  // As with VIA counter readback, the CPU samples the completing edge.
  val busCompleted = completed || finishing
  val busBusy = busy && !finishing
  val busSample = Mux(finishing, values(control(1 downto 0).asUInt) & B(0xfff0, 16 bits), sample)
  // Channel, flag and resolution describe the last completed conversion
  // until the result latch transfers; starting another conversion retains them.
  val busControl = Mux(finishing, control, resultControl)
  io.result := B(0x40, 8 bits)
  switch(io.address) {
    is(0) { io.result := !busCompleted ## !busBusy ## busSample(15 downto 14) ## busControl }
    is(1) { io.result := busSample(15 downto 8) }
    is(2, 3) { io.result := busSample(7 downto 0) }
  }
}
