package leet.bbc

import spinal.core._

/** Synthesizable original-game system at 32 MHz, or 96 MHz with clockMultiplier = 3. Exposes native
  * BBC RGB/sync and an external FM clock/data word store. Simulation-only CPU, RAM and video
  * register diagnostics are not retained. Board clock generation, keyboard transport, storage and
  * display adapters belong in the board integration layer. Pin constraints and routed timing are
  * documented separately.
  */
case class BbcSystemTop(clockMultiplier: Int = 1) extends Component {
  val io = new Bundle {
    val keys = in Bits (128 bits)
    val warmReset = in Bool ()
    val analogue = in Bits (64 bits)
    val joystickButtons = in Bits (2 bits)
    val storageAddress = out UInt (18 bits)
    val storageData = in Bits (16 bits)
    val storageWrite = out Bool ()
    val storageWriteData = out Bits (16 bits)
    val rgb = out Bits (3 bits)
    val hsync = out Bool ()
    val vsync = out Bool ()
    val audio = out Bool ()
    val unsupportedVideo = out Bool ()
  }
  val machine = BbcMachine(debug = false, clockMultiplier = clockMultiplier)
  machine.io.keys := io.keys
  machine.io.warmReset := io.warmReset
  machine.io.debugAddress := 0
  machine.io.analogue := io.analogue
  machine.io.joystickButtons := io.joystickButtons
  machine.io.storageData := io.storageData
  io.storageAddress := machine.io.storageAddress
  io.storageWrite := machine.io.storageWrite
  io.storageWriteData := machine.io.storageWriteData
  io.rgb := machine.io.rgb
  io.hsync := !machine.io.hsync
  io.vsync := !machine.io.vsync
  io.audio := machine.io.audioPdm
  io.unsupportedVideo := machine.io.unsupportedVideo
}

object GenerateSystem extends App {
  SpinalConfig(
    targetDirectory = "build/bbc",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )
    .generateVerilog(BbcSystemTop())
}

/** Common-clock 96 MHz hardware variant; board PLL and timing constraints must select 96 MHz. */
object GenerateSystem96 extends App {
  SpinalConfig(
    targetDirectory = "build/bbc/full-video",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )
    .generateVerilog(BbcSystemTop(3).setDefinitionName("BbcSystem96"))
}

object GenerateVideo96 extends App {
  SpinalConfig(
    targetDirectory = "build/bbc/full-video",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )
    .generateVerilog(BbcVideo(3).setDefinitionName("BbcVideo96"))
}

/** Diagnostic form of the same 96 MHz hardware, with passive capture ports. */
object GenerateMachine96 extends App {
  SpinalConfig(
    targetDirectory = "build/bbc/full-video",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )
    .generateVerilog(BbcMachine(clockMultiplier = 3).setDefinitionName("BbcMachine96"))
}
