package leet.raster

import spinal.core._
import spinal.lib._
import spinal.lib.fsm._
import leet.common._

/** Integer midpoint Bresenham circle rasteriser.
  *
  * Pulse `start` with (center, radius, color) latched. Emits one pixel per cycle across the 8-way
  * mirror — phase 0..7 each maps to one of: (cx+x, cy+y) (cx-x, cy+y) (cx+x, cy-y) (cx-x, cy-y)
  * (cx+y, cy+x) (cx-y, cy+x) (cx+y, cy-x) (cx-y, cy-x) On phase 7, advance the algorithm step: y +=
  * 1; if err < 0: err += 2y+1 else: x -= 1; err += 2(y-x)+1 and exit when y > x.
  *
  * Duplicates at extremes (x==y, y==0) are emitted — fb writes are idempotent so this matches
  * Python oracle output bit-for-bit without de-dup logic.
  */
case class BresenhamCircle(cfg: RenderConfig) extends Component {
  val io = new Bundle {
    val start = in Bool ()
    val center = in(Pixel(cfg))
    val radius = in UInt (10 bits)
    val color = in UInt (cfg.colorBits bits)

    val busy = out Bool ()
    val pixel = master(Flow(Pixel(cfg)))
    val pcol = out UInt (cfg.colorBits bits)
  }

  val xW = io.center.x.getWidth
  val yW = io.center.y.getWidth
  val stepW = (xW max yW) max 11 // unsigned 10-bit radius needs a sign bit
  val outW = stepW + 1 // centre +/- radius must not wrap into visible pixels
  val eW = stepW + 4 // headroom: |err| can reach a few * x at worst

  val cx = Reg(SInt(xW bits))
  val cy = Reg(SInt(yW bits))
  val x = Reg(SInt(stepW bits))
  val y = Reg(SInt(stepW bits))
  val err = Reg(SInt(eW bits))
  val col = Reg(UInt(cfg.colorBits bits))
  val ph = Reg(UInt(3 bits))
  val wideX = SInt(outW bits)
  wideX := 0
  val wideY = SInt(outW bits)
  wideY := 0

  io.busy := True
  io.pixel.valid := False
  io.pixel.payload.x := wideX.resize(xW)
  io.pixel.payload.y := wideY.resize(yW)
  io.pcol := col

  val fsm = new StateMachine {
    val Idle = new State with EntryPoint
    val Draw = new State

    Idle.whenIsActive {
      io.busy := False
      when(io.start) {
        cx := io.center.x
        cy := io.center.y
        x := io.radius.resize(stepW).asSInt
        y := 0
        err := (S(1, eW bits) - io.radius.resize(eW).asSInt)
        col := io.color
        ph := 0
        goto(Draw)
      }
    }

    Draw.whenIsActive {
      io.busy := True
      io.pixel.valid := wideX >= -(1 << (xW - 1)) && wideX < (1 << (xW - 1)) &&
        wideY >= -(1 << (yW - 1)) && wideY < (1 << (yW - 1))
      switch(ph) {
        is(0) {
          wideX := cx.resize(outW) + x.resize(outW)
          wideY := cy.resize(outW) + y.resize(outW)
        }
        is(1) {
          wideX := cx.resize(outW) - x.resize(outW)
          wideY := cy.resize(outW) + y.resize(outW)
        }
        is(2) {
          wideX := cx.resize(outW) + x.resize(outW)
          wideY := cy.resize(outW) - y.resize(outW)
        }
        is(3) {
          wideX := cx.resize(outW) - x.resize(outW)
          wideY := cy.resize(outW) - y.resize(outW)
        }
        is(4) {
          wideX := cx.resize(outW) + y.resize(outW)
          wideY := cy.resize(outW) + x.resize(outW)
        }
        is(5) {
          wideX := cx.resize(outW) - y.resize(outW)
          wideY := cy.resize(outW) + x.resize(outW)
        }
        is(6) {
          wideX := cx.resize(outW) + y.resize(outW)
          wideY := cy.resize(outW) - x.resize(outW)
        }
        is(7) {
          wideX := cx.resize(outW) - y.resize(outW)
          wideY := cy.resize(outW) - x.resize(outW)
        }
      }
      when(ph === 7) {
        // Algorithm step: y' = y + 1; if err < 0 -> err' = err + 2y' + 1, else x' = x - 1; err' = err + 2(y' - x') + 1
        val yNew = (y + 1).resize(stepW bits)
        val takeY = err < 0
        val xNew = Mux(takeY, x, (x - 1).resize(stepW bits))
        val errNew = Mux(
          takeY,
          (err + (yNew.resize(eW bits) |<< 1) + 1).resize(eW bits),
          (err + ((yNew - xNew).resize(eW bits) |<< 1) + 1).resize(eW bits)
        )
        y := yNew
        x := xNew
        err := errNew
        ph := 0
        when(yNew > xNew) { goto(Idle) }
      } otherwise {
        ph := ph + 1
      }
    }
  }
}
