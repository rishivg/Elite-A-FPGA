package leet.render.elite

import spinal.core._
import spinal.lib.fsm._

/** Released LL60..LL50 projection, including LL61's lossy halve/divide/double. Input: x_lo, x_hi=0,
  * x_sign, y_lo, y_hi=0, y_sign, z_lo, z_hi=0. Output: x_lo,x_hi,y_lo,y_hi on the original 256x192
  * view (centre 128,96). Zero divisors/overflow return original (U R)=0x3232 before screen offsets.
  * One shift/subtract divider is shared between x and y; no DSP or ROM.
  */
case class EliteProjection(captureOperands: Boolean = true) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val reduced = in Bits (64 bits)
    val busy = out Bool ()
    val valid = out Bool ()
    val result = out Bits (32 bits)
  }
  val reduced = if (captureOperands) Reg(Bits(64 bits)) init (0) else io.reduced
  val result = Reg(Bits(32 bits)) init (0)
  val axis = RegInit(False)
  val remainder = Reg(UInt(8 bits)) init (0)
  val quotient = Reg(UInt(8 bits)) init (0)
  val bit = Reg(UInt(3 bits)) init (0)
  val shifts = Reg(UInt(4 bits)) init (0)
  val expanded = Reg(UInt(16 bits)) init (0)
  val divisor = reduced(48, 8 bits).asUInt
  val numerator = Mux(axis, reduced(24, 8 bits), reduced(0, 8 bits)).asUInt
  val negative = Mux(axis, reduced(47), reduced(23))
  val (nextRemainder, nextQuotient) = EliteArithmetic.fractionStep(remainder, quotient, divisor)
  val halved = remainder |>> 1
  val doubled = (expanded |<< 1).resize(16)
  io.result := result
  io.busy := True
  io.valid := False

  val fsm = new StateMachine {
    val idle = new State with EntryPoint
    val prepare = new State
    val halve = new State
    val divide = new State
    val expand = new State
    val done = new State

    def store(value: UInt): Unit = {
      val centre = Mux(axis, U(96, 16 bits), U(128, 16 bits))
      // Positive y points up the BBC screen; positive x points right.
      val subtract = Mux(axis, !negative, negative)
      val pixel = Mux(subtract, centre - value, centre + value).resize(16)
      when(axis) {
        result(31 downto 16) := pixel.asBits
        goto(done)
      }
        .otherwise {
          result(15 downto 0) := pixel.asBits
          axis := True
          goto(prepare)
        }
    }
    idle.whenIsActive {
      io.busy := False
      when(io.start) {
        if (captureOperands) reduced := io.reduced
        axis := False
        goto(prepare)
      }
    }
    prepare.whenIsActive {
      remainder := numerator
      quotient := 0
      bit := 0
      shifts := 0
      when(divisor === 0) { store(U(0x3232, 16 bits)) }
        .elsewhen(numerator >= divisor) { goto(halve) }
        .otherwise { goto(divide) }
    }
    halve.whenIsActive {
      remainder := halved
      shifts := shifts + 1
      when(halved < divisor) { goto(divide) }
    }
    divide.whenIsActive {
      remainder := nextRemainder
      quotient := nextQuotient
      when(bit === 7) {
        when(shifts === 0) { store(nextQuotient.resize(16)) }
          .otherwise {
            expanded := nextQuotient.resize(16)
            goto(expand)
          }
      }.otherwise { bit := bit + 1 }
    }
    expand.whenIsActive {
      expanded := doubled
      shifts := shifts - 1
      when(doubled(15)) { store(U(0x3232, 16 bits)) }
        .elsewhen(shifts === 1) { store(doubled) }
    }
    done.whenIsActive {
      io.valid := True
      goto(idle)
    }
  }
}
