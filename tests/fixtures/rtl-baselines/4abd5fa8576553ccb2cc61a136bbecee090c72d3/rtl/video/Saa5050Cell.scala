package leet.video

import spinal.core._

/** Character pipeline, font storage and twelve-dot SAA5050 cell. Resampled weights are diagnostic;
  * board pixel clocks and the CRTC/ULA connection are not part of this component.
  */
case class Saa5050Cell(fontRows: Seq[BigInt], pipelineDepth: Int = 4) extends Component {
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
  // Store one sixty-bit word per character. Both raster lookups share it.
  val font = Mem(Bits(60 bits), 128)
  val words = (0 until 128).map { character =>
    if (character < 96)
      (0 until 10)
        .map { row =>
          fontRows(character * 10 + row) << (row * 6)
        }
        .reduce(_ | _)
    else BigInt(0)
  }
  font.initBigInt(words)
  val valid = attributes.io.character >= 32 && attributes.io.glyphRaster < 20
  val character = Mux(valid, attributes.io.character - 32, U(0, 7 bits))
  val row = (attributes.io.glyphRaster |>> 1).resize(4)
  val neighbour = Mux(attributes.io.glyphRaster(0), row + 1, row - 1)
  val offset = (row.resize(6) |<< 2) + (row.resize(6) |<< 1)
  val neighbourOffset = (neighbour.resize(6) |<< 2) + (neighbour.resize(6) |<< 1)
  val word = font.readAsync(character)
  glyph.io.alpha := Mux(valid, word(offset, 6 bits), B(0, 6 bits))
  glyph.io.neighbour := Mux(
    valid && attributes.io.glyphRaster =/= 0 && attributes.io.glyphRaster =/= 19,
    word(neighbourOffset, 6 bits),
    B(0, 6 bits)
  )
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
