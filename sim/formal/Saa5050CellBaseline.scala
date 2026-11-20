package leet.video

import spinal.core._

/** Straightforward row-ROM baseline for the packed teletext font proof. Shared attribute and glyph
  * components are independently checked against the original model.
  */
case class Saa5050CellBaseline(fontRows: Seq[BigInt]) extends Component {
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
  val attributes = TeletextAttributes()
  attributes.io.enable := io.enable
  attributes.io.operation := io.operation
  attributes.io.data := io.data
  val glyph = TeletextGlyphRow()
  glyph.io.character := attributes.io.character
  glyph.io.glyphSet := attributes.io.glyphSet
  glyph.io.raster := attributes.io.glyphRaster
  val font = Mem(Bits(6 bits), 1024)
  font.initBigInt(fontRows ++ Seq.fill(64)(BigInt(0)))
  val valid = attributes.io.character >= 32 && attributes.io.glyphRaster < 20
  val character = Mux(valid, attributes.io.character - 32, U(0, 7 bits))
  val index = (character.resize(10) * 10 + (attributes.io.glyphRaster |>> 1)).resize(10)
  val neighbour = Mux(attributes.io.glyphRaster(0), index + 1, index - 1)
  glyph.io.alpha := Mux(valid, font.readAsync(index), B(0, 6 bits))
  glyph.io.neighbour := Mux(
    valid && attributes.io.glyphRaster =/= 0 && attributes.io.glyphRaster =/= 19,
    font.readAsync(neighbour),
    B(0, 6 bits)
  )
  io.dots := Mux(attributes.io.suppressed, B(0, 12 bits), glyph.io.dots)
  io.weights := Mux(attributes.io.suppressed, B(0, 32 bits), glyph.io.weights)
  io.foreground := attributes.io.foreground
  io.background := attributes.io.background
}

object GenerateSaa5050CellBaseline extends App {
  val source = scala.io.Source.fromFile("build/bbc/teletext-cells/font.hex")
  val rows =
    try source.getLines().map(line => BigInt(line, 16)).toVector
    finally source.close()
  SpinalConfig(
    targetDirectory = "build/bbc/teletext-proof/golden",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  ).generateVerilog(Saa5050CellBaseline(rows))
}
