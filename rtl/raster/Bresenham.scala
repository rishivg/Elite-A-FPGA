package leet.raster

import spinal.core._
import spinal.lib._
import spinal.lib.fsm._
import leet.common._

/** Octant-general integer Bresenham line rasteriser.
  *
  * Pulse `start` with endpoints `p0`/`p1` latched; the engine then streams one pixel per cycle on
  * the `pixel` Flow (with `pcol`) and holds `busy` high until the final endpoint is emitted. No
  * multiplies, no division — pure adds/compares.
  *
  * Classic integer form (dy stored negative so a single err accumulator covers all eight octants):
  * dx = abs(x1-x0); sx = sign(x1-x0) dy = -abs(y1-y0); sy = sign(y1-y0) err = dx + dy loop: plot;
  * if at end stop; e2 = 2*err; if e2 >= dy {err += dy; x += sx}; if e2 <= dx {err += dx; y += sy}
  */
case class Bresenham(cfg: RenderConfig) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val p0 = in(Pixel(cfg))
    val p1 = in(Pixel(cfg))
    val color = in UInt (cfg.colorBits bits)

    val busy = out Bool ()
    val pixel = master(Flow(Pixel(cfg)))
    val pcol = out UInt (cfg.colorBits bits)
  }

  val xW = io.p0.x.getWidth
  val yW = io.p0.y.getWidth
  val eW = (xW max yW) + 2 // error/accumulator headroom

  val x = Reg(SInt(xW bits))
  val y = Reg(SInt(yW bits))
  val xEnd = Reg(SInt(xW bits))
  val yEnd = Reg(SInt(yW bits))
  val sx = Reg(SInt(2 bits))
  val sy = Reg(SInt(2 bits))
  val dx = Reg(SInt(eW bits)) // +abs(dx)
  val dy = Reg(SInt(eW bits)) // -abs(dy)
  val err = Reg(SInt(eW bits))
  val col = Reg(UInt(cfg.colorBits bits))

  // Watchdog sized to the full coordinate range, so it only ever fires on a truly
  // malformed line (never truncates a legitimate one whose on-screen span is far from
  // p0). The renderer clips to the screen by rejecting out-of-range pixel writes.
  val maxSteps = 1 << io.p0.x.getWidth
  val wd = Reg(UInt(log2Up(maxSteps + 1) bits))

  // defaults
  io.busy := False
  io.pixel.valid := False
  io.pixel.payload.x := x
  io.pixel.payload.y := y
  io.pcol := col

  val fsm = new StateMachine {
    val Idle = new State with EntryPoint
    val Draw = new State

    Idle.whenIsActive {
      when(io.start) {
        // SInt subtraction keeps the operand width. Extend before subtracting
        // so endpoints on opposite sides cannot wrap their delta/sign.
        val ddx = io.p1.x.resize(xW + 1) - io.p0.x.resize(xW + 1)
        val ddy = io.p1.y.resize(yW + 1) - io.p0.y.resize(yW + 1)
        val adx = Mux(ddx < 0, -ddx, ddx) // abs
        val ady = Mux(ddy < 0, -ddy, ddy)
        dx := adx.resize(eW bits)
        dy := (-ady).resize(eW bits)
        err := (adx - ady).resize(eW bits) // = dx + dy
        sx := Mux(io.p0.x < io.p1.x, S(1, 2 bits), S(-1, 2 bits))
        sy := Mux(io.p0.y < io.p1.y, S(1, 2 bits), S(-1, 2 bits))
        x := io.p0.x
        y := io.p0.y
        xEnd := io.p1.x
        yEnd := io.p1.y
        col := io.color
        wd := 0
        goto(Draw)
      }
    }

    Draw.whenIsActive {
      io.busy := True
      io.pixel.valid := True
      wd := wd + 1
      when((x === xEnd && y === yEnd) || wd === maxSteps) {
        goto(Idle)
      } otherwise {
        val e2 = (err << 1).resize(eW bits)
        val stepX = e2 >= dy
        val stepY = e2 <= dx
        when(stepX) { x := (x + sx).resize(xW bits) }
        when(stepY) { y := (y + sy).resize(yW bits) }
        err := (err + Mux(stepX, dy, S(0, eW bits)) + Mux(stepY, dx, S(0, eW bits))).resize(eW bits)
      }
    }
  }
}
