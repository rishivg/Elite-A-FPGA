package leet.video

import spinal.core._

/** SAA5050 current/adjacent font rows. The combinational form shares packed character words; the
  * clocked form uses a dual-port block ROM and returns the preceding edge's rows.
  */
case class TeletextFont(fontRows: Seq[BigInt], synchronous: Boolean = false) extends Component {
  require(fontRows.size == 960 && fontRows.forall(value => value >= 0 && value < 64))
  val io = new Bundle {
    val character = in UInt (7 bits)
    val raster = in UInt (5 bits)
    val alpha = out Bits (6 bits)
    val neighbour = out Bits (6 bits)
  }
  val valid = io.character >= 32 && io.raster < 20
  val character = Mux(valid, io.character - 32, U(0, 7 bits))
  val row = (io.raster |>> 1).resize(4)
  if (synchronous) {
    // Two clocked row ports infer one true dual-port block ROM. Both the data
    // and validity tags belong to the preceding master edge.
    val font = Mem(Bits(6 bits), 1024)
    font.initBigInt(fontRows ++ Seq.fill(64)(BigInt(0)))
    val index = ((character.resize(10) |<< 3) +
      (character.resize(10) |<< 1) + row.resize(10)).resize(10)
    val neighbour = Mux(io.raster(0), index + 1, index - 1)
    val alpha = font.readSync(index)
    val adjacent = font.readSync(neighbour)
    val alphaValid = RegNext(valid) init (False)
    val adjacentValid = RegNext(valid && io.raster =/= 0 && io.raster =/= 19) init (False)
    io.alpha := Mux(alphaValid, alpha, B(0, 6 bits))
    io.neighbour := Mux(adjacentValid, adjacent, B(0, 6 bits))
  } else {
    val font = Mem(Bits(60 bits), 128)
    val words = (0 until 128).map { character =>
      if (character < 96)
        (0 until 10)
          .map(row => fontRows(character * 10 + row) << (row * 6))
          .reduce(_ | _)
      else BigInt(0)
    }
    font.initBigInt(words)
    val neighbour = Mux(io.raster(0), row + 1, row - 1)
    val word = font.readAsync(character)
    // Select a complete six-bit row. Explicit cases avoid a general bit-offset
    // shifter and let native simulation evaluate only the selected slice.
    def lookupRow(index: UInt): Bits = {
      val result = Bits(6 bits)
      result := 0
      switch(index) {
        for (i <- 0 until 10) {
          is(i) { result := word(i * 6, 6 bits) }
        }
      }
      result
    }
    io.alpha := Mux(valid, lookupRow(row), B(0, 6 bits))
    io.neighbour := Mux(
      valid && io.raster =/= 0 && io.raster =/= 19,
      lookupRow(neighbour),
      B(0, 6 bits)
    )
  }
}
