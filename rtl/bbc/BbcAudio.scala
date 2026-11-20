package leet.bbc

import spinal.core._

/** BBC sound acceptance and PSG in the same 32 MHz enable domain. The physical master can be 32 or
  * 96 MHz; acceptance is exported as one master-edge pulse, while PDM holds between enables.
  */
case class BbcAudio(resetPending: Boolean = false) extends Component {
  val io = new Bundle {
    val clock32 = in Bool ()
    val enabled = in Bool ()
    val touch = in Bool ()
    val data = in Bits (8 bits)
    val write = out Bool ()
    val acceptedData = out Bits (8 bits)
    val sample = out UInt (8 bits)
    val pdm = out Bool ()
    val pins = out Bits (4 bits)
  }
  val audio = new ClockEnableArea(io.clock32) {
    val bus = BbcSoundBus(resetPending = resetPending)
    val chip = leet.audio.Psg(
      coreClockHz = 32000000,
      initialTonePeriod = 0,
      halfFirstTick = true,
      bbcDigitalProfile = true
    )
  }
  audio.bus.io.enabled := io.enabled
  audio.bus.io.touch := io.touch
  audio.bus.io.data := io.data
  audio.chip.io.write := audio.bus.io.write
  audio.chip.io.data := audio.bus.io.acceptedData
  io.write := audio.bus.io.write && io.clock32
  io.acceptedData := audio.bus.io.acceptedData
  io.sample := audio.chip.io.sample
  io.pdm := audio.chip.io.pdm
  io.pins := audio.chip.io.noise ## audio.chip.io.tone
}
