package leet.render.elite

import spinal.core._
import spinal.lib.fsm._

/** Released BBC Elite-A LL9 part 5, after LL51 prepares the model-space viewer vector. Inputs and
  * results retain original unsigned magnitudes and sign bits, including truncation, signed zero and
  * overflow-induced scaling. A parent must carry nextDotMagnitude to subsequent faces of the same
  * ship. One shared 8x8 multiplier evaluates the three dot-product terms.
  */
case class EliteFaceVisibility() extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val normal = in Bits (32 bits) // original four face bytes, little endian
    val dotMagnitude = in Vec (UInt(8 bits), 3)
    val dotSign = in Bits (3 bits)
    val scale = in UInt (8 bits) // XX17
    val distance = in UInt (5 bits) // XX4
    val busy = out Bool ()
    val valid = out Bool ()
    val visibility = out UInt (8 bits) // XX2 value; nonzero means visible
    val nextDotMagnitude = out Vec (UInt(8 bits), 3)
  }
  val normalMag = Vec(Reg(UInt(8 bits)) init (0), 3)
  val normalSign = Reg(Bits(3 bits)) init (0)
  val dots = Vec(Reg(UInt(8 bits)) init (0), 3)
  val signs = Reg(Bits(3 bits)) init (0)
  val scale = Reg(UInt(8 bits)) init (0)
  val farFace = RegInit(False)
  val pointMag = Vec(Reg(UInt(8 bits)) init (0), 3)
  val pointSign = Reg(Bits(3 bits)) init (0)
  val termIndex = Reg(UInt(2 bits)) init (0)
  val magnitude = Reg(UInt(8 bits)) init (0)
  val negative = RegInit(False)
  val result = Reg(UInt(8 bits)) init (0)

  // LL38: opposite-sign ties keep the first operand's sign, and same-sign
  // sums wrap to 8 bits. Carry is meaningful only for same-sign overflow.
  def add(a: UInt, sa: Bool, b: UInt, sb: Bool): (UInt, Bool, Bool) = {
    val sum = a +^ b
    val aDominates = a >= b
    val mag = Mux(sa === sb, sum.resize(8), Mux(aDominates, a - b, b - a))
    val sign = Mux(sa === sb, sa, Mux(aDominates, sa, sb))
    (mag, sign, (sa === sb) && sum(8))
  }
  val shiftedNormal = (0 until 3).map(i => (normalMag(i) |>> scale.resize(3)).resize(8))
  val offset = (0 until 3).map(i => add(shiftedNormal(i), normalSign(i), dots(i), signs(i)))
  val overflow = offset.map(_._3).reduce(_ || _)
  val product = pointMag(termIndex) * normalMag(termIndex)
  val term = product(15 downto 8)
  val termSign = pointSign(termIndex) ^ normalSign(termIndex)
  val accumulated = add(magnitude, negative, term, termSign)
  io.visibility := result
  io.nextDotMagnitude := dots
  io.busy := True
  io.valid := False
  val fsm = new StateMachine {
    val idle = new State with EntryPoint
    val prepare = new State
    val multiply = new State
    val done = new State
    idle.whenIsActive {
      io.busy := False
      when(io.start) {
        for (i <- 0 until 3) {
          normalMag(i) := io.normal(8 * (i + 1), 8 bits).asUInt
          normalSign(i) := io.normal(7 - i)
          dots(i) := io.dotMagnitude(i)
        }
        signs := io.dotSign
        scale := io.scale
        farFace := io.normal(4 downto 0).asUInt < io.distance
        goto(prepare)
      }
    }
    prepare.whenIsActive {
      when(farFace) {
        result := 255
        goto(done)
      }
        .elsewhen(scale < 4 && overflow) {
          // ovflw mutates XX18 for every later face, then retries with X=1.
          for (i <- 0 until 3) dots(i) := dots(i) |>> 1
          scale := 1
        }
        .otherwise {
          for (i <- 0 until 3) {
            pointMag(i) := Mux(scale >= 4, dots(i), offset(i)._1)
            pointSign(i) := Mux(scale >= 4, signs(i), offset(i)._2)
          }
          termIndex := 0
          goto(multiply)
        }
    }
    multiply.whenIsActive {
      when(termIndex === 0) {
        magnitude := term
        negative := termSign
        termIndex := 1
      }
        .elsewhen(termIndex === 1) {
          magnitude := accumulated._1
          negative := accumulated._2
          termIndex := 2
        }
        .otherwise {
          result := Mux(accumulated._2, accumulated._1, U(0, 8 bits))
          goto(done)
        }
    }
    done.whenIsActive {
      io.valid := True
      goto(idle)
    }
  }
}
