package leet.video

import spinal.core._

/** SAA5050 glyph rounding and mosaic rows. Alpha inputs are six-bit rows from an external font ROM.
  * The 12-dot mask is separate from the 16-sample resampling weights used by emulator capture.
  */
case class TeletextGlyphRow() extends Component {
  val io = new Bundle {
    val character = in UInt (7 bits)
    val glyphSet = in UInt (2 bits)
    val raster = in UInt (5 bits)
    val alpha = in Bits (6 bits)
    val neighbour = in Bits (6 bits)
    val dots = out Bits (12 bits)
    val weights = out Bits (32 bits)
  }
  val mosaic = io.glyphSet =/= 0 && io.character(5)
  val row = io.raster |>> 1
  val six = Bits(6 bits)
  six := io.alpha
  when(mosaic) {
    val left = Bool()
    val right = Bool()
    left := io.character(4)
    right := io.character(6)
    when(row < 3) {
      left := io.character(0)
      right := io.character(1)
    }.elsewhen(row < 7) {
      left := io.character(2)
      right := io.character(3)
    }
    for (x <- 0 until 6) { six(x) := (if (x < 3) left else right) }
    when(io.glyphSet === 2) {
      six(0) := False
      six(3) := False
      when(row === 2 || row === 6 || row === 9) { six := 0 }
    }
  }
  val current = Bits(12 bits)
  val adjacent = Bits(12 bits)
  for (x <- 0 until 6) {
    current(x * 2, 2 bits) := Mux(six(x), B(3, 2 bits), B(0, 2 bits))
    adjacent(x * 2, 2 bits) := Mux(io.neighbour(x), B(3, 2 bits), B(0, 2 bits))
  }
  val rounded = current | ((current |>> 1) & adjacent & ~(adjacent |>> 1)) |
    ((current |<< 1) & adjacent & ~(adjacent |<< 1))
  val dots = Mux(mosaic, current, rounded)
  val valid = io.character >= 32 && io.raster < 20 && io.glyphSet < 3
  io.dots := Mux(valid, dots, B(0, 12 bits))
  for (group <- 0 until 4) {
    val a = io.dots(group * 3).asUInt.resize(2)
    val b = io.dots(group * 3 + 1).asUInt.resize(2)
    val c = io.dots(group * 3 + 2).asUInt.resize(2)
    io.weights(group * 8, 2 bits) := (a * 3).resize(2).asBits
    io.weights(group * 8 + 2, 2 bits) := (a + (b |<< 1)).asBits
    io.weights(group * 8 + 4, 2 bits) := ((b |<< 1) + c).asBits
    io.weights(group * 8 + 6, 2 bits) := (c * 3).resize(2).asBits
  }
}

object GenerateTeletextGlyphRow extends App {
  SpinalConfig(targetDirectory = "build/bbc/teletext-glyphs")
    .generateVerilog(TeletextGlyphRow())
}
