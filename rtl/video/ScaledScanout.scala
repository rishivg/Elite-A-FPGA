package leet.video

import spinal.core._
import leet.common.{RenderConfig, VideoTiming}

/** Nearest-neighbour scan address from raster timing, without pixel-rate division or
  * multiplication. Counters advance only on pixel/line enables; the current address belongs to the
  * current VGA coordinate, before its edge.
  */
case class ScaledScanout(cfg: RenderConfig, timing: VideoTiming) extends Component {
  require(cfg.screenW > 1 && cfg.screenH > 1 && isPow2(cfg.screenW) && isPow2(cfg.screenH))
  val scale = (timing.hActive / cfg.screenW) min (timing.vActive / cfg.screenH)
  require(scale >= 1)
  val left = (timing.hActive - cfg.screenW * scale) / 2
  val top = (timing.vActive - cfg.screenH * scale) / 2
  val io = new Bundle {
    val x = in UInt (log2Up(timing.hActive) bits)
    val y = in UInt (log2Up(timing.vActive) bits)
    val active = in Bool ()
    val rowActive = in Bool ()
    val pixelTick = in Bool ()
    val lineEnd = in Bool ()
    val address = out UInt (log2Up(cfg.pixels) bits)
    val inside = out Bool ()
  }
  val px = Reg(UInt(log2Up(cfg.screenW) bits)) init (0)
  val py = Reg(UInt(log2Up(cfg.screenH) bits)) init (0)
  val repeatX = Reg(UInt((log2Up(scale) max 1) bits)) init (0)
  val repeatY = Reg(UInt((log2Up(scale) max 1) bits)) init (0)
  val inX = io.x.resize(io.x.getWidth + 1) >= left && io.x.resize(
    io.x.getWidth + 1
  ) < left + cfg.screenW * scale
  val inY = io.rowActive && io.y.resize(io.y.getWidth + 1) >= top && io.y.resize(
    io.y.getWidth + 1
  ) < top + cfg.screenH * scale
  when(io.pixelTick) {
    when(!io.active || !inX) {
      px := 0
      repeatX := 0
    }
      .elsewhen(repeatX === scale - 1) {
        px := px + 1
        repeatX := 0
      }
      .otherwise { repeatX := repeatX + 1 }
  }
  when(io.lineEnd) {
    when(!inY) {
      py := 0
      repeatY := 0
    }
      .elsewhen(repeatY === scale - 1) {
        py := py + 1
        repeatY := 0
      }
      .otherwise { repeatY := repeatY + 1 }
  }
  io.address := (py ## px).asUInt
  io.inside := io.active && inX && inY
}
