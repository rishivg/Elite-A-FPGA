package leet.top

import spinal.core._
import leet.common._
import leet.video._
import leet.soc.Soc

/** Playable SoC, tear-free presentation and RGB+sync scanout in one clock domain. The current
  * firmware uses a 128x128 monochrome frame; display scaling is integer nearest-neighbour with a
  * centred border, preserving native pixels. The declared core frequency also sets the BBC PSG's
  * 250 kHz divider ticks.
  */
case class EliteTop(
    cfg: RenderConfig = RenderConfig(screenW = 128, screenH = 128, viewH = 96, colorBits = 1),
    timing: VideoTiming = VideoTiming(),
    coreClockHz: Int = 25000000,
    pixelDivider: Int = 1
) extends Component {
  require(
    cfg.screenW == 128 && cfg.screenH == 128 && cfg.colorBits == 1,
    "current firmware framebuffer layout is fixed at 128x128 monochrome"
  )
  val io = new Bundle {
    val input = in Bits (32 bits)
    val input2 = in Bits (32 bits)
    val hsync = out Bool ()
    val vsync = out Bool ()
    val r = out UInt (cfg.colorBits bits)
    val g = out UInt (cfg.colorBits bits)
    val b = out UInt (cfg.colorBits bits)
    val audioPdm = out Bool ()
    val audioSample = out UInt (8 bits)
    val active = out Bool ()
    val x = out UInt (log2Up(timing.hActive) bits)
    val y = out UInt (log2Up(timing.vActive) bits)
    val presented = out Bool ()
    val dbg = out Bits (32 bits)
    val trap = out Bool ()
  }

  val vga = VgaTiming(timing, pixelDivider)
  val soc = Soc(rcfg = cfg, psgClockHz = coreClockHz)
  val presenter = FramePresenter(cfg.pixels, cfg.colorBits)
  soc.io.input := io.input
  soc.io.input2 := io.input2
  soc.io.fbRdAddr := presenter.io.sourceAddr
  presenter.io.sourceData := soc.io.fbRdData
  presenter.io.sourceReady := soc.io.frameReady
  soc.io.frameAck := presenter.io.sourceAck
  presenter.io.blankStart := vga.io.blankStart

  val scanout = ScaledScanout(cfg, timing)
  scanout.io.x := vga.io.x
  scanout.io.y := vga.io.y
  scanout.io.active := vga.io.active
  scanout.io.rowActive := vga.io.rowActive
  scanout.io.pixelTick := vga.io.pixelTick
  scanout.io.lineEnd := vga.io.lineEnd
  presenter.io.scanAddr := scanout.io.address
  // RAM data arrives one core clock later. Delay coordinates, blanking and
  // both syncs by exactly that latency so the first/last pixel are not shifted.
  val visible = RegNext(scanout.io.inside && presenter.io.valid) init (False)
  val pix = Mux(visible, presenter.io.scanData, U(0, cfg.colorBits bits))
  io.hsync := RegNext(vga.io.hsync) init (True)
  io.vsync := RegNext(vga.io.vsync) init (True)
  io.r := pix
  io.g := pix
  io.b := pix
  io.active := RegNext(vga.io.active) init (False)
  io.x := RegNext(vga.io.x) init (0)
  io.y := RegNext(vga.io.y) init (0)
  io.presented := presenter.io.presented
  io.audioPdm := soc.io.audioPdm
  io.audioSample := soc.io.audioSample
  io.dbg := soc.io.dbg
  io.trap := soc.io.trap
}
