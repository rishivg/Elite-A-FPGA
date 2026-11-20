package leet.render.elite

import spinal.core._
import spinal.lib.fsm._

/** Released LL17/LL21 orientation preparation, plus optional LL42 transpose. Input is INWK bytes
  * 9..26: nose, roof, side; nine little-endian coordinates. ROL extracts n=(high[6:0]<<1)|low[7].
  * LL28 yields floor(256*n/197), capped at 255. The original complete high byte is retained. Output
  * rows are side, roof, nose, or their transpose for original vertex transforms. One shared
  * shift/subtract divider needs no DSP or ROM.
  */
case class EliteOrientation(captureOperands: Boolean = true) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val raw = in Bits (144 bits)
    val transpose = in Bool ()
    val busy = out Bool ()
    val valid = out Bool ()
    val result = out Bits (144 bits)
  }
  val raw = if (captureOperands) Reg(Bits(144 bits)) init (0) else io.raw
  val transpose = RegInit(False)
  val result = Reg(Bits(144 bits)) init (0)
  val index = Reg(UInt(4 bits)) init (0)
  val row = Reg(UInt(2 bits)) init (0)
  val column = Reg(UInt(2 bits)) init (0)
  val bit = Reg(UInt(3 bits)) init (0)
  val remainder = Reg(UInt(8 bits)) init (0)
  val quotient = Reg(UInt(8 bits)) init (0)
  val coordinates = Vec(Seq(6, 7, 8, 3, 4, 5, 0, 1, 2).map(i => raw(16 * i, 16 bits)))
  val coordinate = coordinates(index)
  val numerator = (coordinate(14 downto 8) ## coordinate(7)).asUInt
  val (nextRemainder, nextQuotient) =
    EliteArithmetic.fractionStep(remainder, quotient, U(197, 8 bits))
  val destination =
    Mux(transpose, ((column.resize(4) |<< 1) + column.resize(4) + row.resize(4)).resize(4), index)
  val output = Vec((0 until 9).map(i => result(16 * i, 16 bits)))
  io.result := result
  io.busy := True
  io.valid := False
  val fsm = new StateMachine {
    val idle = new State with EntryPoint
    val prepare = new State
    val divide = new State
    val done = new State
    def store(value: UInt): Unit = {
      output(destination) := coordinate(15 downto 8) ## value.asBits
      when(index === 8) { goto(done) }.otherwise {
        index := index + 1
        when(column === 2) {
          column := 0
          row := row + 1
        }.otherwise { column := column + 1 }
        goto(prepare)
      }
    }
    idle.whenIsActive {
      io.busy := False
      when(io.start) {
        if (captureOperands) raw := io.raw
        transpose := io.transpose
        index := 0
        row := 0
        column := 0
        goto(prepare)
      }
    }
    prepare.whenIsActive {
      when(numerator >= 197) { store(U(255, 8 bits)) }.otherwise {
        remainder := numerator
        quotient := 0
        bit := 0
        goto(divide)
      }
    }
    divide.whenIsActive {
      remainder := nextRemainder
      quotient := nextQuotient
      when(bit === 7) { store(nextQuotient) }.otherwise { bit := bit + 1 }
    }
    done.whenIsActive {
      io.valid := True
      goto(idle)
    }
  }
}
