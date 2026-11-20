package leet.bbc

import spinal.core._

/** 6845 register bus and bitmap Video ULA at a 32 MHz master clock. Video fetches synchronous RAM
  * on phases shared with the CPU; captures observe emitted RGB. Teletext and interlaced sync/video
  * are exposed as unsupported modes pending implementation. Elite-A's bitmap display is the first
  * compatibility target.
  */
case class BbcVideo() extends Component {
  val io = new Bundle {
    val crtcWrite = in Bool ()
    val ulaWrite = in Bool ()
    val address = in Bool ()
    val data = in Bits (8 bits)
    val result = out Bits (8 bits)
    val scroll = in UInt (2 bits)
    val memoryAddress = out UInt (15 bits)
    val memoryRead = out Bool ()
    val memoryData = in Bits (8 bits)
    val pixel = out Bool ()
    val display = out Bool ()
    val rgb = out Bits (3 bits)
    val x = out UInt (11 bits)
    val y = out UInt (10 bits)
    val hsync = out Bool ()
    val vsync = out Bool ()
    val busVsync = out Bool ()
    val frame = out Bool ()
    val control = out Bits (8 bits)
    val registers = out Bits (144 bits)
    val palette = out Bits (64 bits)
    val unsupported = out Bool ()
  }
  val masks =
    Seq(255, 255, 255, 255, 127, 31, 127, 127, 243, 31, 127, 31, 63, 255, 63, 255, 63, 255)
  val regs = Vec.fill(18)(Reg(Bits(8 bits)) init (0))
  val index = Reg(UInt(5 bits)) init (0)
  val control = Reg(Bits(8 bits)) init (0)
  val palette = Vec.fill(16)(Reg(Bits(4 bits)) init (0))
  when(io.crtcWrite) {
    when(!io.address) { index := io.data(4 downto 0).asUInt }
      .elsewhen(index < 18) { regs(index) := io.data & Vec(masks.map(v => B(v, 8 bits)))(index) }
  }
  when(io.ulaWrite) {
    when(!io.address) { control := io.data }
      .otherwise { palette(io.data(7 downto 4).asUInt) := io.data(3 downto 0) }
  }
  io.result := 0
  when(io.address && index >= 12 && index < 18) { io.result := regs(index) }
  io.control := control
  io.registers := regs.asBits
  io.palette := palette.asBits
  io.unsupported := control(1) || regs(8)(0)

  val master = Reg(UInt(5 bits)) init (16)
  val endChar = Mux(control(4), master(3 downto 0) === 15, master === 31)
  master := master + 1
  val crtc = Crtc6845()
  crtc.io.enable := endChar
  crtc.io.registers := regs.asBits
  val horizontal = crtc.io.horizontal
  val raster = crtc.io.raster
  val line = crtc.io.line
  val ma = crtc.io.address
  io.vsync := crtc.io.vsync
  io.busVsync := crtc.io.busVsync
  io.hsync := crtc.io.hsync
  io.frame := crtc.io.frame
  val subtract = Vec(U(8, 4 bits), U(4, 4 bits), U(10, 4 bits), U(5, 4 bits))(io.scroll)
  val high = UInt(4 bits)
  high := ma(11 downto 8)
  when(ma(12)) { high := ma(11 downto 8) - subtract }
  io.memoryAddress := high @@ ma(7 downto 0) @@ raster(2 downto 0)
  when(ma(13)) {
    io.memoryAddress := Mux(ma(11), U(0x7c00, 15 bits), U(0x3c00, 15 bits)) | ma(9 downto 0).resize(
      15
    )
  }
  io.memoryRead := Mux(control(4), master(3 downto 0) === 0, master === 16)
  val physicalPixel =
    Mux(control(4), master(3 downto 1).resize(4), (master + U(16, 5 bits))(4 downto 1))
  val repeat = U(3, 2 bits) - control(3 downto 2).asUInt
  val shift = (physicalPixel |>> repeat).resize(4)
  val shifted =
    ((io.memoryData.asUInt.resize(16) |<< shift) | ((U(1, 16 bits) |<< shift) - 1)).resize(8).asBits
  val colourIndex = (shifted(7) ## shifted(5) ## shifted(3) ## shifted(1)).asUInt
  val colour = palette(colourIndex)
  io.rgb := Mux(colour(3) && control(0), colour(2 downto 0), colour(2 downto 0) ^ B(7, 3 bits))
  val skew = regs(8)(5 downto 4).asUInt
  val cellHorizontal = RegNextWhen(horizontal, io.memoryRead) init (0)
  val cellLine = RegNextWhen(line, io.memoryRead) init (0)
  val cellDisplay = RegNextWhen(
    horizontal >= skew && horizontal < regs(1).asUInt + skew.resize(
      8
    ) && crtc.io.displayActive && !raster(3) && skew =/= 3 && !control(1),
    io.memoryRead
  ) init (False)
  io.display := cellDisplay
  io.pixel := master(0)
  io.x := ((cellHorizontal - skew.resize(8))
    .resize(11) |<< Mux(control(4), U(3, 3 bits), U(4, 3 bits))) + physicalPixel.resize(11)
  io.y := cellLine
  when(!io.display) { io.rgb := 0 }
}
