package leet.top

import spinal.core._

/** Arty A7 digital integration: 100 MHz system clock, 25 MHz pixel enables (640x480 at 59.52 Hz),
  * debounced controls and PDM audio. Pin assignments and the attached VGA/audio/input peripherals
  * remain board-specific constraints.
  */
case class ArtyA7() extends Component {
  val io = new Bundle {
    val axes = in Bits (6 bits)
    val buttons = in Bits (8 bits)
    val buttons2 = in Bits (6 bits)
    val hsync = out Bool ()
    val vsync = out Bool ()
    val r = out UInt (2 bits)
    val g = out UInt (2 bits)
    val b = out UInt (2 bits)
    val audioPdm = out Bool ()
  }
  val controls = BoardInputs(debounceCycles = 100000)
  controls.io.axes := io.axes
  controls.io.buttons := io.buttons
  controls.io.buttons2 := io.buttons2
  val core = EliteTop(coreClockHz = 100000000, pixelDivider = 4)
  core.io.input := controls.io.input
  core.io.input2 := controls.io.input2
  io.hsync := core.io.hsync
  io.vsync := core.io.vsync
  io.r := Mux(core.io.r =/= 0, U(3, 2 bits), U(0, 2 bits))
  io.g := io.r
  io.b := io.r
  io.audioPdm := core.io.audioPdm
}
