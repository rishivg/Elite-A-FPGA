package leet.bbc

import spinal.core._

/** 6845 register bus and bitmap Video ULA at a 32 MHz master clock. Video fetches synchronous RAM
  * on phases shared with the CPU; captures observe emitted RGB. The 96 MHz option also emits
  * twelve-dot teletext cells at 12 MHz; the 32 MHz option reports teletext as unsupported.
  */
case class BbcVideo(clockMultiplier: Int = 1) extends Component {
  require(clockMultiplier == 1 || clockMultiplier == 3)
  val io = new Bundle {
    val crtcWrite = in Bool ()
    val lightPenPulse = in Bool ()
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
  val writableRegs = Vec.fill(16)(Reg(Bits(8 bits)) init (0))
  val lightPen = Reg(UInt(14 bits)) init (0)
  val regs = Vec(
    writableRegs.toSeq ++ Seq(lightPen(13 downto 8).asBits.resize(8), lightPen(7 downto 0).asBits)
  )
  val index = Reg(UInt(5 bits)) init (0)
  val control = Reg(Bits(8 bits)) init (0)
  // Valid bits preserve zero-after-reset semantics without resetting distributed RAM.
  val palette = Mem(Bits(4 bits), 16)
  val paletteValid = Reg(Bits(16 bits)) init (0)
  val paletteWrite = io.ulaWrite && io.address
  palette.write(io.data(7 downto 4).asUInt, io.data(3 downto 0), enable = paletteWrite)
  when(paletteWrite) { paletteValid(io.data(7 downto 4).asUInt) := True }
  // Keep the packed diagnostic output in one register. It is updated only
  // with palette writes and is removed when hardware drops that output.
  val paletteMirror = Reg(Bits(64 bits)) init (0)
  when(paletteWrite) {
    paletteMirror(io.data(7 downto 4).asUInt @@ U(0, 2 bits), 4 bits) := io.data(3 downto 0)
  }
  when(io.crtcWrite) {
    when(!io.address) { index := io.data(4 downto 0).asUInt }
      .otherwise {
        // Apply each register's constant mask at its write enable. This lets
        // synthesis remove unused register bits instead of a shared mask mux.
        for (register <- 0 until 16) {
          when(index === register) {
            writableRegs(register) := io.data & B(masks(register), 8 bits)
          }
        }
      }
  }
  when(io.ulaWrite && !io.address) { control := io.data }
  io.result := 0
  when(io.address && index >= 12 && index < 18) { io.result := regs(index) }
  io.control := control
  io.registers := regs.asBits
  io.palette := paletteMirror
  io.unsupported := control(1)

  // A common 96 MHz clock divides exactly into 32, 16 and 12 MHz. Keep
  // the legacy 32 MHz bitmap path available without approximating dot clocks.
  val clock32 =
    if (clockMultiplier == 1) True
    else {
      val divider = Reg(UInt(2 bits)) init (0)
      divider := Mux(divider === 2, U(0, 2 bits), divider + 1)
      divider === 2
    }
  val master = Reg(UInt(5 bits)) init (16)
  val endChar = Mux(control(4), master(3 downto 0) === 15, master === 31)
  when(clock32) { master := master + 1 }
  val crtc = Crtc6845()
  crtc.io.enable := clock32 && endChar
  crtc.io.registers := regs.asBits
  when(io.lightPenPulse) { lightPen := crtc.io.busAddress }
  val horizontal = crtc.io.horizontal
  val raster = crtc.io.raster
  val line = crtc.io.line
  val ma = crtc.io.address
  io.vsync := crtc.io.vsync
  io.busVsync := crtc.io.busVsync
  io.hsync := crtc.io.hsync
  io.frame := crtc.io.frame
  val memory = BbcVideoAddress()
  memory.io.ma := ma
  memory.io.raster := crtc.io.rasterPins(2 downto 0)
  memory.io.scroll := io.scroll
  memory.io.fast := control(4)
  memory.io.teletext := control(1)
  memory.io.oddTick := master(4)
  io.memoryAddress := memory.io.address
  io.memoryRead := clock32 && Mux(control(4), master(3 downto 0) === 0, master === 16)
  val physicalPixel =
    Mux(control(4), master(3 downto 1).resize(4), (master + U(16, 5 bits))(4 downto 1))
  val repeat = U(3, 2 bits) - control(3 downto 2).asUInt
  val shift = (physicalPixel |>> repeat).resize(4)
  val shifted =
    ((io.memoryData.asUInt.resize(16) |<< shift) | ((U(1, 16 bits) |<< shift) - 1)).resize(8).asBits
  val colourIndex = (shifted(7) ## shifted(5) ## shifted(3) ## shifted(1)).asUInt
  val colour = Mux(paletteValid(colourIndex), palette.readAsync(colourIndex), B(0, 4 bits))
  val bitmapRgb =
    Mux(colour(3) && control(0), colour(2 downto 0), colour(2 downto 0) ^ B(7, 3 bits))
  val skew = regs(8)(5 downto 4).asUInt
  val cellHorizontal = RegNextWhen(horizontal, io.memoryRead) init (0)
  val interlacedVideo = regs(8)(1 downto 0) === B(3, 2 bits)
  val outputLine = Mux(interlacedVideo, (line |<< 1) | crtc.io.oddField.asUInt.resize(10), line)
  val cellLine = RegNextWhen(outputLine, io.memoryRead) init (0)
  val cellDisplay = RegNextWhen(
    horizontal >= skew && horizontal < regs(1).asUInt + skew.resize(
      8
    ) && crtc.io.displayActive && !raster(3) && skew =/= 3 && !control(1),
    io.memoryRead
  ) init (False)
  // Cursor segments continue across character cells, including blanked RGB.
  // Skew 3 disables the cursor; the ULA selects three segment widths.
  val cursorIndex = Reg(UInt(3 bits)) init (0)
  val cursorAddress = (regs(14)(5 downto 0) ## regs(15)).asUInt
  val cursorStart = ma === cursorAddress && crtc.io.cursorRaster &&
    crtc.io.displayActive && horizontal < regs(1).asUInt
  val selectedCursor = Mux(cursorStart, U(3, 3 bits) - regs(8)(7 downto 6).asUInt, cursorIndex)
  val cursorSegment = Bool()
  cursorSegment := False
  switch(selectedCursor) {
    is(3) { cursorSegment := control(7) }
    is(4) { cursorSegment := control(6) }
    is(5, 6) { cursorSegment := control(5) }
  }
  val cellCursor = RegNextWhen(crtc.io.cursorVisible && cursorSegment, io.memoryRead) init (False)
  when(io.memoryRead && selectedCursor =/= 0) {
    cursorIndex := Mux(selectedCursor === 6, U(0, 3 bits), selectedCursor + 1)
  }
  when(io.memoryRead && cursorStart && selectedCursor === 0) { cursorIndex := 0 }
  io.display := cellDisplay || cellCursor
  io.pixel := clock32 && master(0)
  io.x := ((cellHorizontal - skew.resize(8))
    .resize(11) |<< Mux(control(4), U(3, 3 bits), U(4, 3 bits))) + physicalPixel.resize(11)
  io.y := cellLine
  val blankedRgb = Mux(cellDisplay, bitmapRgb, B(0, 3 bits))
  io.rgb := Mux(cellCursor, blankedRgb ^ B(7, 3 bits), blankedRgb)

  if (clockMultiplier == 3) {
    val source = scala.io.Source.fromFile("build/bbc/teletext-font.hex")
    val font =
      try source.getLines().map(line => BigInt(line, 16)).toVector
      finally source.close()
    // IC15 is separate from the chip's three character stages. Blanking
    // forces bit 6 on the byte already held by IC15, before it enters the chip.
    // Combining these into four ungated stages leaks border controls into rows.
    val cell = leet.video.Saa5050Cell(font, pipelineDepth = 3)
    val inputLatch = Reg(Bits(8 bits)) init (0)
    val stage = master - U(16, 5 bits)
    val horizontalDisplay = horizontal < regs(1).asUInt + skew.resize(8) + 2
    val inputDisplay = crtc.io.displayActive && horizontal >= skew &&
      horizontal < regs(1).asUInt + skew.resize(8)
    val disptmg = crtc.io.displayActive && horizontalDisplay && skew =/= 3
    cell.io.enable := False
    cell.io.operation := 0
    cell.io.data := 0
    // IC15's MA13 gate qualifies input. During horizontal blanking IC37/36
    // force bit 6 high; all character clocks still traverse the chip pipeline.
    when(clock32) {
      switch(stage) {
        is(1) {
          cell.io.enable := True
          cell.io.operation := 1
          inputLatch := Mux(ma(13), io.memoryData, B(0, 8 bits))
          cell.io.data := Mux(inputDisplay, inputLatch, inputLatch | B(0x40, 8 bits))
        }
        // The character clock precedes the scanline reset. The unchanged
        // beebjit teletext_data endpoint processes its queued character before
        // applying the pipelined LOSE transition as well.
        is(7) {
          cell.io.enable := True; cell.io.operation := 3; cell.io.data(0) := ma(13) && disptmg
        }
        is(3) { cell.io.enable := True; cell.io.operation := 2; cell.io.data(0) := crtc.io.vsync }
        is(4) {
          cell.io.enable := True; cell.io.operation := 4; cell.io.data(0) := crtc.io.rasterPins(0)
        }
        is(5) { cell.io.enable := True; cell.io.operation := 5 }
      }
    }
    val dotPhase = Reg(UInt(3 bits)) init (0)
    dotPhase := dotPhase + 1
    val dotTick = dotPhase === 7
    val dot = Reg(UInt(4 bits)) init (0)
    val dots = Reg(Bits(12 bits)) init (0)
    val foreground = Reg(Bits(3 bits)) init (0)
    val background = Reg(Bits(3 bits)) init (0)
    val cursor = RegInit(False)
    val shown = RegInit(False)
    val positionX = Reg(UInt(11 bits)) init (0)
    val positionY = Reg(UInt(10 bits)) init (0)
    val load = clock32 && stage === 6
    when(dotTick) { dot := Mux(dot === 11, U(0, 4 bits), dot + 1) }
    when(load) {
      dot := 0
      dots := cell.io.dots
      foreground := cell.io.foreground.asBits
      background := cell.io.background.asBits
      cursor := cellCursor
      shown := !control(4) && crtc.io.displayActive && ma(13) && skew =/= 3 &&
        horizontal >= skew.resize(8) + 2 && horizontal < regs(1).asUInt + skew.resize(8) + 2
      val column = (cellHorizontal - skew.resize(8) - 2).resize(11)
      positionX := ((column |<< 3) + (column |<< 2)).resize(11)
      positionY := cellLine
    }
    when(control(1)) {
      io.unsupported := control(4) // 2 MHz teletext tricks still need a separate pipeline.
      io.pixel := dotTick && !load
      io.display := shown || cursor
      io.x := positionX + dot.resize(11)
      io.y := positionY
      val teletextRgb = Mux(shown, Mux(dots(dot), foreground, background), B(0, 3 bits))
      io.rgb := Mux(cursor, teletextRgb ^ B(7, 3 bits), teletextRgb)
    }
  }
}
