package leet.bbc

import spinal.core._

/** 6522 CA2/CB2 output modes at a one-MHz accepted bus endpoint. Explicit edge reports preserve a
  * pulse ending and restarting on the same edge; final levels alone would lose that transition.
  */
case class ViaControlPins() extends Component {
  val io = new Bundle {
    val tick = in Bool ()
    val write = in Bool ()
    val address = in UInt (4 bits)
    val data = in Bits (8 bits)
    val ca1 = in Bool ()
    val cb1 = in Bool ()
    val cb1Pulse = in Bool ()
    val pcr = out Bits (8 bits)
    val ca2 = out Bool ()
    val cb2 = out Bool ()
    val ca2Output = out Bool ()
    val cb2Output = out Bool ()
    val ca2Rise = out Bool ()
    val cb2Rise = out Bool ()
    val ca2Fall = out Bool ()
    val cb2Fall = out Bool ()
  }
  val pcr = Reg(Bits(8 bits)) init (0)
  val ca2 = RegInit(False)
  val cb2 = RegInit(False)
  val pulseA = RegInit(False)
  val pulseB = RegInit(False)
  val previousCa1 = RegNext(io.ca1) init (False)
  val previousCb1 = RegNext(io.cb1) init (False)
  // write is already an acknowledged bus event; tick clocks pulse expiry.
  val accepted = io.write
  val pcrWrite = accepted && io.address === 12
  val nextPcr = Mux(pcrWrite, io.data, pcr)
  val activeCa1 = io.ca1 =/= previousCa1 && io.ca1 === pcr(0)
  val activeCb1 = (io.cb1 =/= previousCb1 && io.cb1 === pcr(4)) || io.cb1Pulse
  val automaticA = (io.tick && pulseA) || (activeCa1 && pcr(3 downto 2) === B(2, 2 bits))
  val automaticB = (io.tick && pulseB) || (activeCb1 && pcr(7 downto 6) === B(2, 2 bits))
  val beforeWriteA = ca2 || automaticA
  val beforeWriteB = cb2 || automaticB
  val nextA = Bool()
  val nextB = Bool()
  nextA := beforeWriteA
  nextB := beforeWriteB
  when(pcrWrite && io.data(3)) { nextA := io.data(3 downto 1) =/= B(6, 3 bits) }
  when(pcrWrite && io.data(7)) { nextB := io.data(7 downto 5) =/= B(6, 3 bits) }
  when(accepted && io.address === 1 && pcr(3 downto 2) === B(2, 2 bits)) { nextA := False }
  when(accepted && io.address === 0 && pcr(7 downto 6) === B(2, 2 bits)) { nextB := False }
  ca2 := nextA
  cb2 := nextB
  pcr := nextPcr
  when(io.tick) {
    pulseA := False
    pulseB := False
    when(accepted && io.address === 1 && pcr(3 downto 1) === B(5, 3 bits)) { pulseA := True }
    when(accepted && io.address === 0 && pcr(7 downto 5) === B(5, 3 bits)) { pulseB := True }
  }
  io.pcr := pcr
  io.ca2 := ca2
  io.cb2 := cb2
  io.ca2Output := pcr(3)
  io.cb2Output := pcr(7)
  // Scheduler pin changes precede an accepted bus write. Keep both events
  // when a pulse expires and that write immediately lowers the output again.
  io.ca2Rise := (!ca2 && beforeWriteA && pcr(3)) || (!beforeWriteA && nextA && nextPcr(3))
  io.cb2Rise := (!cb2 && beforeWriteB && pcr(7)) || (!beforeWriteB && nextB && nextPcr(7))
  io.ca2Fall := beforeWriteA && !nextA && nextPcr(3)
  io.cb2Fall := beforeWriteB && !nextB && nextPcr(7)
}
