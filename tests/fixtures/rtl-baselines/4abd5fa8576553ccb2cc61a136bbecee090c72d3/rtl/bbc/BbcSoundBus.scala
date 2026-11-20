package leet.bbc

import spinal.core._

/** Accept the latest data after the declared seven-microsecond WE window. Data changes while WE
  * remains low schedule another acceptance. A VIA port update also handles writing the same byte
  * again. Changes during a pending window do not restart it. WE is checked at its scheduled
  * deadline; a brief high pulse preserves that deadline, including early re-enabling. This matches
  * the original emulator's stable-window interface; physical SN76489 acceptance relative to its
  * internal clock remains unqualified.
  */
case class BbcSoundBus() extends Component {
  val io = new Bundle {
    val enabled = in Bool ()
    val touch = in Bool ()
    val data = in Bits (8 bits)
    val write = out Bool ()
    val acceptedData = out Bits (8 bits)
  }
  val previousData = RegNext(io.data) init (0)
  val previousEnabled = RegNext(io.enabled) init (False)
  val delay = Reg(UInt(8 bits)) init (225)
  // 225 is the idle sentinel, so a separate pending flip-flop is unnecessary.
  // The original stable-window task keeps its deadline when WE goes high.
  // WE is tested at acceptance; re-enabling before then uses that deadline.
  val changed = io.touch || io.data =/= previousData || !previousEnabled
  when(delay < 224) { delay := delay + 1 }
    .elsewhen(delay === 224) { delay := Mux(io.enabled && changed, U(1, 8 bits), U(225, 8 bits)) }
    .elsewhen(io.enabled && changed) { delay := 1 }
  // A scheduled acceptance completes before a CPU write at the same edge.
  // Preserve that interval's data/WE, then start or cancel the next window.
  io.write := previousEnabled && delay === 224
  io.acceptedData := previousData
}
