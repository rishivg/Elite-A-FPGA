package leet.video

import spinal.core._

/** Copy a held SoC frame into the back scanout bank, then swap in vertical blanking. The renderer
  * cannot modify its source until that swap is accepted. All ports use the same clock; sourceData
  * and scanData have one-cycle latency.
  */
case class FramePresenter(pixels: Int = 16384, colorBits: Int = 1) extends Component {
  require(pixels > 1 && isPow2(pixels), "bank addressing requires a power-of-two frame")
  val addressWidth = log2Up(pixels)
  val io = new Bundle {
    val sourceReady = in Bool ()
    val sourceAddr = out UInt (addressWidth bits)
    val sourceData = in UInt (colorBits bits)
    val sourceAck = out Bool ()
    val blankStart = in Bool ()
    val scanAddr = in UInt (addressWidth bits)
    val scanData = out UInt (colorBits bits)
    val valid = out Bool ()
    val presented = out Bool ()
  }
  object State extends SpinalEnum { val IDLE, COPY, WAIT_BLANK = newElement() }
  val state = Reg(State()) init (State.IDLE)
  val frontBank = RegInit(False)
  val frontValid = RegInit(False)
  val address = Reg(UInt(addressWidth bits)) init (0)
  val issuedAll = RegInit(False)
  val issue = state === State.COPY && !issuedAll
  val readValid = RegNext(issue) init (False)
  val writeAddr = RegNext(address) init (0)
  val bank = Mem(UInt(colorBits bits), pixels * 2)
  bank.write((!frontBank ## writeAddr).asUInt, io.sourceData, enable = readValid)
  io.scanData := bank.readSync((frontBank ## io.scanAddr).asUInt)
  io.sourceAddr := address
  io.sourceAck := False
  io.presented := False
  io.valid := frontValid

  switch(state) {
    is(State.IDLE) {
      when(io.sourceReady) {
        address := 0
        issuedAll := False
        state := State.COPY
      }
    }
    is(State.COPY) {
      when(issue) {
        when(address === pixels - 1) { issuedAll := True }
          .otherwise { address := address + 1 }
      }
      // Last read returns a cycle later; finish only after its RAM write.
      when(readValid && writeAddr === pixels - 1) { state := State.WAIT_BLANK }
    }
    is(State.WAIT_BLANK) {
      when(io.blankStart) {
        frontBank := !frontBank
        frontValid := True
        io.sourceAck := True
        io.presented := True
        state := State.IDLE
      }
    }
  }
}
