package leet.video

import spinal.core._

/** Character pipeline, font storage and twelve-dot SAA5050 cell. Resampled weights are diagnostic;
  * board pixel clocks and the CRTC/ULA connection are not part of this component.
  */
case class Saa5050Cell(
    fontRows: Seq[BigInt],
    pipelineDepth: Int = 4,
    fontSynchronous: Boolean = false
) extends Component {
  require(fontRows.size == 960 && fontRows.forall(value => value >= 0 && value < 64))
  val io = new Bundle {
    val enable = in Bool ()
    val operation = in UInt (3 bits)
    val data = in Bits (8 bits)
    val dots = out Bits (12 bits)
    val weights = out Bits (32 bits)
    val foreground = out UInt (3 bits)
    val background = out UInt (3 bits)
  }
  val attributes = TeletextAttributes(pipelineDepth)
  attributes.io.enable := io.enable
  attributes.io.operation := io.operation
  attributes.io.data := io.data
  val glyph = TeletextGlyphRow()
  glyph.io.character := attributes.io.character
  glyph.io.glyphSet := attributes.io.glyphSet
  glyph.io.raster := attributes.io.glyphRaster
  val font = TeletextFont(fontRows, synchronous = fontSynchronous)
  font.io.character := attributes.io.character
  font.io.raster := attributes.io.glyphRaster
  glyph.io.alpha := font.io.alpha
  glyph.io.neighbour := font.io.neighbour
  io.dots := Mux(attributes.io.suppressed, B(0, 12 bits), glyph.io.dots)
  io.weights := Mux(attributes.io.suppressed, B(0, 32 bits), glyph.io.weights)
  io.foreground := attributes.io.foreground
  io.background := attributes.io.background
}

object GenerateSaa5050Cell extends App {
  val source = scala.io.Source.fromFile("build/bbc/teletext-cells/font.hex")
  val rows =
    try source.getLines().map(line => BigInt(line, 16)).toVector
    finally source.close()
  SpinalConfig(
    targetDirectory = "build/bbc/teletext-cells",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  ).generateVerilog(Saa5050Cell(rows))
}
