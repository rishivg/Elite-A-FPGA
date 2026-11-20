package leet.bbc

import spinal.core._

/** Enabled Hitachi 6845 counters and address sequencing. Register inputs settle after the clocked
  * bus endpoint. All state changes are gated by enable, so idle master clocks can be skipped by the
  * probe.
  */
case class Crtc6845() extends Component {
  val io = new Bundle {
    val enable = in Bool ()
    val registers = in Bits (144 bits)
    val horizontal = out UInt (8 bits)
    val row = out UInt (7 bits)
    val raster = out UInt (5 bits)
    val rasterPins = out UInt (5 bits)
    val oddField = out Bool ()
    val line = out UInt (10 bits)
    val address = out UInt (14 bits)
    val busAddress = out UInt (14 bits)
    val vsync = out Bool ()
    val busVsync = out Bool ()
    val hsync = out Bool ()
    val frame = out Bool ()
    val displayActive = out Bool ()
    val cursorRaster = out Bool ()
    val cursorVisible = out Bool ()
  }
  val regs = Vec((0 until 18).map(i => io.registers(i * 8, 8 bits)))
  val horizontal = Reg(UInt(8 bits)) init (0)
  val row = Reg(UInt(7 bits)) init (0)
  val raster = Reg(UInt(5 bits)) init (0)
  val line = Reg(UInt(10 bits)) init (0)
  val adjusting = RegInit(False)
  val adjust = Reg(UInt(5 bits)) init (0)
  val lineAddress = Reg(UInt(14 bits)) init (0)
  val nextLineAddress = Reg(UInt(14 bits)) init (0)
  val address = Reg(UInt(14 bits)) init (0)
  val nextAddress = UInt(14 bits)
  nextAddress := address
  val sync = RegInit(False)
  val syncLines = Reg(UInt(4 bits)) init (0)
  val hadSync = RegInit(False)
  val frame = False
  val endMain = RegInit(False)
  val checkAdjust = RegInit(False)
  val endAdjust = RegInit(False)
  val endDummy = RegInit(False)
  val frameParity = RegInit(False)
  // Blink advances at the end of vertical display and latches at field start.
  val fieldCount = Reg(UInt(5 bits)) init (0)
  // Raster start/end are sticky within a row, including split cursor ranges.
  val cursorOn = RegInit(False)
  val cursorOff = RegInit(False)
  val cursorVisible = RegInit(False)
  val fieldOdd = RegInit(False)
  val rasterPin0 = RegInit(False)
  val verticalActive = RegInit(False)
  val firstLine = RegInit(True)
  val nextRow = UInt(7 bits)
  nextRow := row
  val nextRaster = UInt(5 bits)
  nextRaster := raster
  val nextFirstLine = Bool()
  nextFirstLine := firstLine
  val nextVerticalActive = Bool()
  nextVerticalActive := verticalActive
  val nextMain = Bool()
  nextMain := endMain
  val nextCheck = Bool()
  nextCheck := checkAdjust
  val nextAdjustEnd = Bool()
  nextAdjustEnd := endAdjust
  val nextAdjust = UInt(5 bits)
  nextAdjust := adjust
  val adjustEnding = endAdjust || (checkAdjust && endMain && adjust === regs(5).asUInt)
  val newField = Bool()
  newField := False
  val endLine = horizontal === regs(0).asUInt
  val endRow = raster === regs(9).asUInt
  val interlaceVideo = regs(8)(1 downto 0) === B(3, 2 bits)
  val syncPoint =
    !regs(8)(0) || fieldOdd || horizontal.resize(9) === ((regs(0).asUInt.resize(9) + 1) |>> 1)
  val nextSync = Bool()
  nextSync := sync
  val syncEnding = sync && syncLines === regs(3)(7 downto 4).asUInt && syncPoint
  val syncStarting = row === regs(7).asUInt && !hadSync && (!sync || syncEnding) && syncPoint
  val advancedLineAddress = Mux(horizontal === regs(1).asUInt, address, nextLineAddress)
  when(io.enable) {
    nextAddress := address + 1
    when(horizontal === regs(1).asUInt) { nextLineAddress := address }
    when(syncEnding) { nextSync := False }
    when(syncStarting) { nextSync := True }
    when(syncStarting && !syncEnding) {
      hadSync := True
      syncLines := 0
    }
    when(checkAdjust) {
      nextCheck := False
      when(endMain) {
        when(adjust === regs(5).asUInt) { nextAdjustEnd := True }
        nextAdjust := adjust + 1
      }
    }
    when(horizontal === 1) {
      when(row === regs(4).asUInt && endRow) {
        nextMain := True
        nextAdjust := 0
      }
      nextCheck := True
    }
    when(endLine) {
      when(raster === regs(11).asUInt) { cursorOff := True }
      horizontal := 0
      line := line + 1
      nextFirstLine := False
      syncLines := Mux(syncStarting && !syncEnding, U(1, 4 bits), syncLines + 1)
      nextRaster := Mux(interlaceVideo, (raster + 2) & U(30, 5 bits), raster + 1)
      nextAddress := Mux(endRow, advancedLineAddress, lineAddress)
      when(endRow) { lineAddress := advancedLineAddress }
      when(!adjusting && endRow) {
        cursorOn := False
        cursorOff := False
        nextRow := row + 1
        nextRaster := 0
        hadSync := False
      }
      when(nextMain && !adjustEnding) { adjusting := True }
      when(endDummy) { newField := True }
      when(adjustEnding) {
        adjusting := False
        when(regs(8)(0) && frameParity) { endDummy := True }.otherwise { newField := True }
      }
      when(newField) {
        cursorOn := False
        cursorOff := False
        val blink = regs(10)(6 downto 5)
        cursorVisible := blink === B(0, 2 bits) ||
          (blink === B(2, 2 bits) && fieldCount(3)) ||
          (blink === B(3, 2 bits) && fieldCount(4))
        nextRow := 0
        nextRaster := 0
        line := 0
        adjusting := False
        nextMain := False
        nextAdjustEnd := False
        endDummy := False
        nextFirstLine := True
        nextVerticalActive := True
        fieldOdd := frameParity
        hadSync := False
        lineAddress := (regs(12)(5 downto 0) ## regs(13)).asUInt
        nextLineAddress := (regs(12)(5 downto 0) ## regs(13)).asUInt
        nextAddress := (regs(12)(5 downto 0) ## regs(13)).asUInt
        frame := True
      }
      when(nextRaster === regs(10)(4 downto 0).asUInt) { cursorOn := True }
    }.otherwise { horizontal := horizontal + 1 }
    when(nextRow === regs(6).asUInt && !nextFirstLine && verticalActive) {
      nextVerticalActive := False
      frameParity := !frameParity
      fieldCount := fieldCount + 1
    }
  }
  // External RA0 is latched at line start. In interlace sync/video it
  // includes the field parity, while the internal raster count stays even.
  when(io.enable && endLine) {
    rasterPin0 := nextRaster(0) ^ (interlaceVideo && frameParity)
  }
  row := nextRow
  raster := nextRaster
  firstLine := nextFirstLine
  verticalActive := nextVerticalActive
  endMain := nextMain
  checkAdjust := nextCheck
  endAdjust := nextAdjustEnd
  adjust := nextAdjust
  sync := nextSync
  val hsync = RegInit(False)
  val hcount = Reg(UInt(4 bits)) init (0)
  val hEnding = hsync && hcount + 1 === regs(3)(3 downto 0).asUInt
  when(io.enable) {
    when(hsync) {
      hcount := hcount + 1
      when(hEnding) { hsync := False }
    }
    when(horizontal === regs(2).asUInt && (!hsync || hEnding)) {
      hsync := True
      hcount := 0
    }
  }
  io.horizontal := horizontal
  io.row := row
  io.raster := raster
  io.rasterPins := raster
  io.rasterPins(0) := rasterPin0
  io.oddField := fieldOdd
  io.line := line
  address := nextAddress
  io.address := address
  // Accepted peripheral events follow the CRTC clock on this master edge.
  io.busAddress := nextAddress
  io.frame := frame
  io.displayActive := verticalActive
  io.cursorRaster := cursorOn && !cursorOff
  io.cursorVisible := cursorVisible
  io.vsync := sync
  io.busVsync := Mux(io.enable, nextSync, sync)
  io.hsync := hsync
}
