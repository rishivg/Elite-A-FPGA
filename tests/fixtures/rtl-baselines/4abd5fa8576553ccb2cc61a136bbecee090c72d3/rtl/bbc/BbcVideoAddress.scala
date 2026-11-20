package leet.bbc

import spinal.core._

/** IC32/IC39 DRAM address wiring, including the Model B linear-address pages. */
case class BbcVideoAddress() extends Component {
  val io = new Bundle {
    val ma = in UInt (14 bits)
    val raster = in UInt (3 bits)
    val scroll = in UInt (2 bits)
    val fast = in Bool ()
    val teletext = in Bool ()
    val oddTick = in Bool ()
    val address = out UInt (15 bits)
  }
  val subtract = Vec(U(8, 4 bits), U(4, 4 bits), U(10, 4 bits), U(5, 4 bits))(io.scroll)
  val high = Mux(io.ma(12), io.ma(11 downto 8) - subtract, io.ma(11 downto 8))
  io.address := high @@ io.ma(7 downto 0) @@ io.raster
  when(io.ma(13)) {
    val linear = Mux(io.ma(11), U(0x7c00, 15 bits), U(0x3c00, 15 bits)) |
      io.ma(9 downto 0).resize(15)
    io.address := linear
    // In fast bitmap mode the refresh phase XORs MA6. The teletext latch
    // and the slow bitmap clock both select the non-XORed location.
    when(io.fast && !io.teletext && !io.oddTick) { io.address := linear ^ U(0x40, 15 bits) }
  }
}
