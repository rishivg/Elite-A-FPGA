package leet.render

import spinal.core._
import spinal.lib.fsm._
import leet.common._

/** Perspective projection: screen_x = cx + focal*x/z, screen_y = cy - focal*y/z.
  *
  * View-space vertex in (Q8.8); pulse `start`, then `valid` pulses with the screen pixel. Because
  * both x/z and y/z share the Q8.8 scale of z, the 2^FRAC factors cancel and `|coord_fixed| * focal
  * / z_fixed` lands directly in pixels — so the divider operates on the raw fixed-point words with
  * no extra shifts.
  *
  * z is clamped to a near-plane minimum (>=1 LSB) so the divider never sees zero. The renderer
  * rejects invalid/overflowed vertices; this unit does not clip edges against the near plane.
  */
case class Project(cfg: RenderConfig, focal: Int = 200) extends Component {
  require(focal > 0 && focal <= 1023, "26-bit numerator must hold every Q8.8 input times focal")
  val io = new Bundle {
    val start = in Bool ()
    val v = in(Vec3()) // view-space, Q8.8; z should be > 0
    val busy = out Bool ()
    val valid = out Bool ()
    val p = out(Pixel(cfg))
    val overflow = out Bool () // valid result exceeds Pixel's signed range
  }

  val numW = 26
  val denW = Fixed.WIDTH // 16

  val dvx = Divider(numW, denW)
  val dvy = Divider(numW, denW)

  // latched operands
  val numX = Reg(UInt(numW bits))
  val numY = Reg(UInt(numW bits))
  val zMag = Reg(UInt(denW bits))
  val sgnX = Reg(Bool())
  val sgnY = Reg(Bool())

  dvx.io.numer := numX
  dvx.io.denom := zMag
  dvx.io.start := False
  dvy.io.numer := numY
  dvy.io.denom := zMag
  dvy.io.start := False

  val cx = cfg.screenW / 2
  val cy = cfg.viewHeight / 2

  io.busy := True
  io.valid := False
  io.overflow := False
  io.p.x := 0
  io.p.y := 0

  val fsm = new StateMachine {
    val Idle = new State with EntryPoint
    val Kick = new State
    val Wait = new State

    Idle.whenIsActive {
      io.busy := False
      when(io.start) {
        val ax = Mux(io.v.x < 0, -io.v.x, io.v.x).asUInt.resize(Fixed.WIDTH bits)
        val ay = Mux(io.v.y < 0, -io.v.y, io.v.y).asUInt.resize(Fixed.WIDTH bits)
        numX := (ax * U(focal)).resize(numW bits)
        numY := (ay * U(focal)).resize(numW bits)
        zMag := Mux(io.v.z > 0, io.v.z.asUInt, U(1, denW bits))
        sgnX := io.v.x < 0
        sgnY := io.v.y < 0
        goto(Kick)
      }
    }

    Kick.whenIsActive {
      dvx.io.start := True
      dvy.io.start := True
      goto(Wait)
    }

    Wait.whenIsActive {
      when(dvx.io.valid) {
        // Preserve the unsigned quotient's magnitude through sign and centre
        // addition. Truncating to 16 bits here can turn a distant offscreen
        // projection into an apparently valid onscreen coordinate.
        val limit = BigInt(1) << (io.p.x.getWidth - 1)
        // Check the full magnitudes against sign-specific bounds before
        // narrowing. This avoids wide signed adders on the pixel output path.
        val overflowX = Mux(sgnX, dvx.io.quotient > limit + cx, dvx.io.quotient > limit - 1 - cx)
        val overflowY = Mux(sgnY, dvy.io.quotient > limit - 1 - cy, dvy.io.quotient > limit + cy)
        val resultW = io.p.x.getWidth + 1
        val qx = dvx.io.quotient.resize(io.p.x.getWidth).resize(resultW).asSInt
        val qy = dvy.io.quotient.resize(io.p.y.getWidth).resize(resultW).asSInt
        val qxS = Mux(sgnX, -qx, qx)
        val qyS = Mux(sgnY, -qy, qy)
        val x = S(cx, resultW bits) + qxS
        val y = S(cy, resultW bits) - qyS
        io.overflow := overflowX || overflowY
        when(!io.overflow) {
          io.p.x := x.resize(io.p.x.getWidth bits)
          io.p.y := y.resize(io.p.y.getWidth bits)
        }
        io.valid := True
        goto(Idle)
      }
    }
  }
}
