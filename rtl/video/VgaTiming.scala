package leet.video

import spinal.core._
import leet.common.VideoTiming

/** Generic VGA/CRT timing generator. Drives hsync/vsync and exposes the current active-region pixel
  * coordinate. Clock this domain at the pixel clock (e.g. 25.175 MHz for 640x480@60). This block is
  * complete and synthesizable.
  */
case class VgaTiming(t: VideoTiming = VideoTiming(), pixelDivider: Int = 1) extends Component {
  require(pixelDivider >= 1)
  val io = new Bundle {
    val hsync = out Bool ()
    val vsync = out Bool ()
    val active = out Bool ()
    val x = out UInt (log2Up(t.hActive) bits)
    val y = out UInt (log2Up(t.vActive) bits)
    val blankStart = out Bool ()
    val pixelTick = out Bool ()
    val lineEnd = out Bool ()
    val rowActive = out Bool ()
  }

  val hCnt = Reg(UInt(log2Up(t.hTotal) bits)) init (0)
  val vCnt = Reg(UInt(log2Up(t.vTotal) bits)) init (0)

  val hEnd = hCnt === (t.hTotal - 1)
  val vEnd = vCnt === (t.vTotal - 1)

  val pixelTick =
    if (pixelDivider == 1) True
    else {
      val divide = Reg(UInt(log2Up(pixelDivider) bits)) init (0)
      val tick = divide === pixelDivider - 1
      divide := Mux(tick, U(0), divide + 1)
      tick
    }
  when(pixelTick) { hCnt := Mux(hEnd, U(0), hCnt + 1) }
  when(hEnd && pixelTick) {
    vCnt := Mux(vEnd, U(0), vCnt + 1)
  }

  val hActiveR = hCnt < t.hActive
  val vActiveR = vCnt < t.vActive

  val hSyncStart = t.hActive + t.hFront
  val vSyncStart = t.vActive + t.vFront
  val hInSync = (hCnt >= hSyncStart) && (hCnt < hSyncStart + t.hSync)
  val vInSync = (vCnt >= vSyncStart) && (vCnt < vSyncStart + t.vSync)

  io.hsync := hInSync ^ Bool(!t.hSyncPos)
  io.vsync := vInSync ^ Bool(!t.vSyncPos)
  io.active := hActiveR && vActiveR
  io.x := hCnt.resize(io.x.getWidth)
  io.y := vCnt.resize(io.y.getWidth)
  io.blankStart := pixelTick && hCnt === 0 && vCnt === t.vActive
  io.pixelTick := pixelTick
  io.lineEnd := pixelTick && hEnd
  io.rowActive := vActiveR
}
