package leet

import spinal.core._
import leet.top._
import leet.common.RenderConfig
import leet.render.Renderer
import leet.soc.Soc
import leet.audio.Psg

/** Verilog generation entry point. `sbt "runMain leet.Generate"` (or just `sbt run`) emits Verilog
  * for each top into build/rtl/.
  */
object Generate extends App {
  val cfg = SpinalConfig(
    targetDirectory = "build/rtl",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )

  cfg.generateVerilog(EliteTop())
  cfg.generateVerilog(ArtyA7())
  cfg.generateVerilog(Ulx3s())
  cfg.generateVerilog(Renderer(RenderConfig(screenW = 128, screenH = 96, colorBits = 1)))
  cfg.generateVerilog(Psg())
  cfg.generateVerilog(Soc())

  println("Generated Verilog -> build/rtl/")
}

/** Dedicated elaboration for the shared native frame-regression runner. */
object GenerateSocRegression extends App {
  SpinalConfig(
    targetDirectory = "build/demo",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  ).generateVerilog(Soc())
}
