package leet.bbc

import spinal.core._

/** IC32 and the Model B keyboard wiring, including accepted GPIO endpoints. */
case class BbcSystemPins() extends Component {
  val io = new Bundle {
    val keys = in Bits (128 bits)
    val driveA = in Bits (8 bits)
    val busDriveA = in Bits (8 bits)
    val busPortB = in Bits (8 bits)
    val portAIn = out Bits (8 bits)
    val ca2 = out Bool ()
    val busCa2 = out Bool ()
    val scroll = out UInt (2 bits)
    val soundEnabled = out Bool ()
    val soundData = out Bits (8 bits)
    val latch = out Bits (8 bits)
  }
  val latch = Reg(Bits(8 bits)) init (0)
  val previousPortB = RegNext(io.busPortB) init (0)
  val nextLatch = Bits(8 bits)
  nextLatch := latch
  when(io.busPortB =/= previousPortB) {
    nextLatch(io.busPortB(2 downto 0).asUInt) := io.busPortB(3)
  }
  latch := nextLatch
  io.latch := latch
  io.scroll := latch(5 downto 4).asUInt
  val columns = Vec((0 until 16).map(i => io.keys(8 * i, 8 bits)))
  val column = io.driveA(3 downto 0).asUInt
  val row = io.driveA(6 downto 4).asUInt
  io.portAIn := B(255, 8 bits)
  when(!latch(3)) { io.portAIn(7) := columns(column)(row) }
  val anyKey = (0 until 13).map(i => columns(i)(7 downto 1).orR).reduce(_ || _)
  io.ca2 := Mux(latch(3), anyKey, columns(column)(7 downto 1).orR && column < 13)
  val busColumn = io.busDriveA(3 downto 0).asUInt
  val busRow = io.busDriveA(6 downto 4).asUInt
  io.busCa2 := Mux(nextLatch(3), anyKey, columns(busColumn)(7 downto 1).orR && busColumn < 13)
  io.soundEnabled := !nextLatch(0)
  io.soundData := io.busDriveA
  when(!nextLatch(3)) { io.soundData(7) := io.busDriveA(7) && columns(busColumn)(busRow) }
}
