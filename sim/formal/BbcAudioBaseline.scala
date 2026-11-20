package leet.bbc

import spinal.core._

/** Previous 32 MHz sound wiring with the corrected active-bus reset window. Both sides use the same
  * qualified child implementations; this proves clock-domain wiring preservation.
  */
case class BbcAudioBaseline() extends Component {
  val io = new Bundle {
    val enabled = in Bool ()
    val touch = in Bool ()
    val data = in Bits (8 bits)
    val write = out Bool ()
    val acceptedData = out Bits (8 bits)
    val sample = out UInt (8 bits)
    val pdm = out Bool ()
    val pins = out Bits (4 bits)
  }
  val audio_bus = BbcSoundBus(resetPending = true)
  val audio_chip = leet.audio.Psg(
    coreClockHz = 32000000,
    initialTonePeriod = 0,
    halfFirstTick = true,
    bbcDigitalProfile = true
  )
  audio_bus.io.enabled := io.enabled
  audio_bus.io.touch := io.touch
  audio_bus.io.data := io.data
  audio_chip.io.write := audio_bus.io.write
  audio_chip.io.data := audio_bus.io.acceptedData
  io.write := audio_bus.io.write
  io.acceptedData := audio_bus.io.acceptedData
  io.sample := audio_chip.io.sample
  io.pdm := audio_chip.io.pdm
  io.pins := audio_chip.io.noise ## audio_chip.io.tone
}

object GenerateBbcAudioBaseline extends App {
  SpinalConfig(
    targetDirectory = "build/bbc/audio-proof/golden",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  ).generateVerilog(BbcAudioBaseline())
}
