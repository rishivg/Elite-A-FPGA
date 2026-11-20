package leet.render.elite

import spinal.core._
import spinal.lib.fsm._

/** Released LL51: three rows of three unsigned products, each truncated before signed-magnitude
  * addition. The complete sign byte is retained: LL38 flips only bit 7 when the second operand
  * dominates. Same-sign sums wrap at 8 bits. Matrix is three rows of (magnitude, sign byte) pairs;
  * vector/result are three such pairs, all packed little endian. One shared multiplier takes nine
  * cycles. With captureOperands=false, the parent must hold operands throughout busy.
  */
case class EliteMatrixVector(captureOperands: Boolean = true) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val vector = in Bits (48 bits)
    val matrix = in Bits (144 bits)
    val busy = out Bool ()
    val valid = out Bool ()
    val result = out Bits (48 bits)
  }
  val vector = if (captureOperands) Reg(Bits(48 bits)) init (0) else io.vector
  val matrix = if (captureOperands) Reg(Bits(144 bits)) init (0) else io.matrix
  val result = Reg(Bits(48 bits)) init (0)
  val row = Reg(UInt(2 bits)) init (0)
  val column = Reg(UInt(2 bits)) init (0)
  val index = Reg(UInt(4 bits)) init (0)
  val magnitude = Reg(UInt(8 bits)) init (0)
  val sign = Reg(Bits(8 bits)) init (0)
  val vm = Vec((0 until 3).map(i => vector(16 * i, 8 bits).asUInt))
  val vs = Vec((0 until 3).map(i => vector(16 * i + 8, 8 bits)))
  val mm = Vec((0 until 9).map(i => matrix(16 * i, 8 bits).asUInt))
  val ms = Vec((0 until 9).map(i => matrix(16 * i + 8, 8 bits)))
  val product = vm(column) * mm(index)
  val term = product(15 downto 8)
  val termSign = vs(column) ^ ms(index)
  val sameSign = sign(7) === termSign(7)
  val firstDominates = magnitude >= term
  val sum = Mux(
    sameSign,
    (magnitude + term).resize(8),
    Mux(firstDominates, magnitude - term, term - magnitude)
  )
  val sumSign = sign ^ Mux(!sameSign && !firstDominates, B(0x80, 8 bits), B(0, 8 bits))
  val values = Vec((0 until 3).map(i => result(16 * i, 16 bits)))
  io.result := result
  io.busy := True
  io.valid := False
  val fsm = new StateMachine {
    val idle = new State with EntryPoint
    val multiply = new State
    val done = new State
    idle.whenIsActive {
      io.busy := False
      when(io.start) {
        if (captureOperands) {
          vector := io.vector
          matrix := io.matrix
        }
        row := 0
        column := 0
        index := 0
        goto(multiply)
      }
    }
    multiply.whenIsActive {
      when(column === 0) {
        magnitude := term
        sign := termSign
        column := 1
        index := index + 1
      }
        .elsewhen(column === 1) {
          magnitude := sum
          sign := sumSign
          column := 2
          index := index + 1
        }
        .otherwise {
          values(row) := sumSign ## sum.asBits
          when(row === 2) { goto(done) }
            .otherwise {
              row := row + 1
              column := 0
              index := index + 1
            }
        }
    }
    done.whenIsActive {
      io.valid := True
      goto(idle)
    }
  }
}
