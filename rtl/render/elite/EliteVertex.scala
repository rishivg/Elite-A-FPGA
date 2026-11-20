package leet.render.elite

import spinal.core._
import spinal.lib.fsm._

/** Released LL9 after LL51: translate, scale all axes together, then project. World is three
  * (lo,hi,sign-byte) triples. Rotated is three (mag,sign-byte) pairs from LL51. x/y sums wrap at 16
  * bits and retain world sign-byte tags. Original z ignores the world sign, and clamps to 4 only
  * after subtraction. Reduced bytes are x_lo,0,x_sign,y_lo,0,y_sign,z_lo,0 (LL60 operands).
  */
case class EliteVertex(captureOperands: Boolean = true) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val world = in Bits (72 bits)
    val rotated = in Bits (48 bits)
    val busy = out Bool ()
    val valid = out Bool ()
    val reduced = out Bits (64 bits)
    val result = out Bits (32 bits)
  }
  val world = if (captureOperands) Reg(Bits(72 bits)) init (0) else io.world
  val rotated = if (captureOperands) Reg(Bits(48 bits)) init (0) else io.rotated
  val magnitudes = Vec(Reg(UInt(16 bits)) init (0), 3)
  val signs = Vec(Reg(Bits(8 bits)) init (0), 2)
  val axis = Reg(UInt(2 bits)) init (0)
  val centres = Vec((0 until 3).map(i => world(24 * i, 16 bits).asUInt))
  val centreSigns = Vec((0 until 3).map(i => world(24 * i + 16, 8 bits)))
  val offsets = Vec((0 until 3).map(i => rotated(16 * i, 8 bits).asUInt.resize(16)))
  val offsetSigns = Vec((0 until 3).map(i => rotated(16 * i + 8, 8 bits)))
  val centre = centres(axis)
  val offset = offsets(axis)
  val opposite = Mux(axis === 2, offsetSigns(axis)(7), centreSigns(axis)(7) ^ offsetSigns(axis)(7))
  val underflow = centre < offset
  val difference = (centre - offset).resize(16)
  val magnitude =
    Mux(opposite, Mux(underflow, offset - centre, difference), (centre + offset).resize(16))
  val sign = centreSigns(axis) ^ Mux(opposite && underflow, B(0x80, 8 bits), B(0, 8 bits))
  val reduced = Reg(Bits(64 bits)) init (0)
  val projection = EliteProjection(captureOperands = false)
  projection.io.reduced := reduced
  projection.io.start := False
  io.reduced := reduced
  io.result := projection.io.result
  io.busy := True
  io.valid := False

  val fsm = new StateMachine {
    val idle = new State with EntryPoint
    val translate = new State
    val scale = new State
    val project = new State
    val waitProject = new State
    val done = new State
    idle.whenIsActive {
      io.busy := False
      when(io.start) {
        if (captureOperands) {
          world := io.world
          rotated := io.rotated
        }
        axis := 0

        goto(translate)
      }
    }
    translate.whenIsActive {
      magnitudes(axis) := magnitude
      when(axis === 2) {
        when(opposite && (underflow || difference < 4)) { magnitudes(2) := 4 }
        goto(scale)
      }.otherwise {
        signs(axis.resize(1)) := sign
        axis := axis + 1
      }
    }
    scale.whenIsActive {
      when(magnitudes.map(_(15 downto 8) =/= 0).reduce(_ || _)) {
        for (i <- 0 until 3) magnitudes(i) := magnitudes(i) |>> 1
      }.otherwise {
        reduced := magnitudes(2).asBits ## signs(1) ## magnitudes(1).asBits ## signs(
          0
        ) ## magnitudes(0).asBits
        goto(project)
      }
    }
    project.whenIsActive {
      projection.io.start := True
      goto(waitProject)
    }
    waitProject.whenIsActive { when(projection.io.valid) { goto(done) } }
    done.whenIsActive {
      io.valid := True
      goto(idle)
    }
  }
}
