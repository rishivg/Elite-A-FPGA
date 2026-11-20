package leet.top

import spinal.core._

/** ULX3S digital integration using the 25 MHz oscillator: debounced controls, RGB+sync and PDM
  * sound. This is an RGB interface, not a GPDI/TMDS encoder; peripheral wiring/pins and an HDMI
  * encoder still require a selected board.
  */
case class Ulx3s() extends Component {
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
  val controls = BoardInputs(debounceCycles = 25000)
  controls.io.axes := io.axes
  controls.io.buttons := io.buttons
  controls.io.buttons2 := io.buttons2
  val core = EliteTop(coreClockHz = 25000000)
  core.io.input := controls.io.input
  core.io.input2 := controls.io.input2
  io.hsync := core.io.hsync
  io.vsync := core.io.vsync
  io.r := Mux(core.io.r =/= 0, U(3, 2 bits), U(0, 2 bits))
  io.g := io.r
  io.b := io.r
  io.audioPdm := core.io.audioPdm
}
