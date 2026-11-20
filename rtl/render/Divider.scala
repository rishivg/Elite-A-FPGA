package leet.render

import spinal.core._

/** Sequential restoring unsigned divider. Latency = `nW` cycles after `start`; `valid` pulses for
  * one cycle when `quotient`/`remainder` are ready.
  *
  * Accepts start only while idle. The caller must supply a nonzero denominator; Project clamps z to
  * a near-plane minimum before starting the divider.
  */
case class Divider(nW: Int, dW: Int) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val numer = in UInt (nW bits)
    val denom = in UInt (dW bits)
    val busy = out Bool ()
    val valid = out Bool ()
    val quotient = out UInt (nW bits)
    val remainder = out UInt (dW bits)
  }

  val remW = dW + 1
  val acc = Reg(UInt(remW bits)) init (0)
  val quo = Reg(UInt(nW bits)) init (0)
  val den = Reg(UInt(dW bits)) init (0)
  val cnt = Reg(UInt(log2Up(nW + 1) bits)) init (0)
  val run = RegInit(False)
  val done = RegInit(False)

  done := False
  io.busy := run
  io.valid := done
  io.quotient := quo
  io.remainder := acc.resize(dW bits)

  when(!run) {
    when(io.start) {
      acc := 0
      quo := io.numer
      den := io.denom
      cnt := nW
      run := True
    }
  } otherwise {
    val denE = den.resize(remW bits)
    val shifted = (acc(remW - 2 downto 0) ## quo(nW - 1)).asUInt // (acc<<1) | quo.msb
    val fits = shifted >= denE
    acc := Mux(fits, (shifted - denE).resize(remW bits), shifted)
    quo := (quo(nW - 2 downto 0) ## fits).asUInt // (quo<<1) | fits
    cnt := cnt - 1
    when(cnt === 1) {
      run := False
      done := True
    }
  }
}
