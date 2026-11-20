package leet.video

import spinal.core._

/** SAA5050 character attributes and input pipeline. Glyph ROM, pixel serialization and board timing
  * are separate responsibilities; this component does not enable teletext scanout.
  */
case class TeletextAttributes(pipelineDepth: Int = 4) extends Component {
  require(pipelineDepth == 3 || pipelineDepth == 4)
  val io = new Bundle {
    val enable = in Bool ()
    // 0 idle, 1 character input, 2 DEW, 3 DISPTMG, 4 RA0, 5 character clock.
    val operation = in UInt (3 bits)
    val data = in Bits (8 bits)
    val character = out UInt (7 bits)
    val glyphSet = out UInt (2 bits)
    val glyphRaster = out UInt (5 bits)
    val foreground = out UInt (3 bits)
    val background = out UInt (3 bits)
    val suppressed = out Bool ()
    val attributes = out Bits (31 bits)
  }
  val queue = Vec.fill(pipelineDepth)(Reg(UInt(7 bits)) init (0))
  val colour = Reg(UInt(3 bits)) init (7)
  val background = Reg(UInt(3 bits)) init (0)
  val separated = RegInit(False)
  val doubleHeight = RegInit(False)
  val secondHalf = RegInit(False)
  val wasDouble = RegInit(False)
  val graphics = RegInit(False)
  val conceal = RegInit(False)
  val flashing = RegInit(False)
  val hold = RegInit(False)
  val held = Reg(UInt(7 bits)) init (0)
  val heldSet = Reg(UInt(2 bits)) init (0)
  val nextSet = Reg(UInt(2 bits)) init (0)
  val scanline = Reg(UInt(4 bits)) init (0)
  val flashCount = Reg(UInt(6 bits)) init (0)
  val dew = RegInit(False)
  val disptmg = RegInit(False)
  val ra0 = RegInit(False)
  val cellCharacter = Reg(UInt(7 bits)) init (32)
  val cellSet = Reg(UInt(2 bits)) init (0)
  val cellForeground = Reg(UInt(3 bits)) init (0)
  val cellBackground = Reg(UInt(3 bits)) init (0)
  val cellDouble = RegInit(False)
  val cellConceal = RegInit(False)
  val cellFlash = RegInit(False)

  val nextColour = UInt(3 bits)
  val nextBackground = UInt(3 bits)
  val nextSeparated = Bool()
  val nextDouble = Bool()
  val nextGraphics = Bool()
  val nextConceal = Bool()
  val nextFlash = Bool()
  val nextHold = Bool()
  nextColour := colour
  nextBackground := background
  nextSeparated := separated
  nextDouble := doubleHeight
  nextGraphics := graphics
  nextConceal := conceal
  nextFlash := flashing
  nextHold := hold
  val code = queue(0)
  val isControl = code < 32
  when(isControl) {
    when((code >= 1 && code <= 7) || (code >= 17 && code <= 23)) {
      nextGraphics := code(4)
      nextColour := code(2 downto 0)
      nextConceal := False
    }
    switch(code) {
      is(8) { nextFlash := True }
      is(9) { nextFlash := False }
      is(12, 13) { nextDouble := code(0) }
      is(24) { nextConceal := True }
      is(25) { nextSeparated := False }
      is(26) { nextSeparated := True }
      is(28) { nextBackground := 0 }
      is(29) { nextBackground := colour }
      is(30) { nextHold := True }
      is(31) { nextHold := False }
    }
  }
  val renderedCharacter = UInt(7 bits)
  val renderedSet = UInt(2 bits)
  renderedCharacter := code
  renderedSet := nextSet
  val useHeld = graphics && (hold || nextHold) && nextDouble === doubleHeight
  when(isControl) {
    renderedCharacter := 32
    when(useHeld) {
      renderedCharacter := held
      renderedSet := heldSet
    }
  }

  when(io.enable) {
    switch(io.operation) {
      is(1) {
        for (i <- 0 until pipelineDepth - 1) { queue(i) := queue(i + 1) }
        queue(pipelineDepth - 1) := io.data(6 downto 0).asUInt
      }
      is(2) {
        dew := io.data(0)
        when(dew && !io.data(0)) {
          scanline := 0
          secondHalf := False
          flashCount := flashCount + 1
        }
      }
      is(3) {
        disptmg := io.data(0)
        when(disptmg && !io.data(0)) {
          colour := 7
          background := 0
          hold := False
          held := 32
          nextSet := 0
          heldSet := 0
          flashing := False
          separated := False
          graphics := False
          conceal := False
          doubleHeight := False
          scanline := scanline + 1
          when(scanline === 9) {
            scanline := 0
            secondHalf := !secondHalf && wasDouble
          }
          wasDouble := False
        }
      }
      is(4) { ra0 := io.data(0) }
      is(5) {
        colour := nextColour
        background := nextBackground
        separated := nextSeparated
        doubleHeight := nextDouble
        graphics := nextGraphics
        conceal := nextConceal
        flashing := nextFlash
        hold := nextHold
        nextSet := Mux(nextGraphics, Mux(nextSeparated, U(2, 2 bits), U(1, 2 bits)), U(0, 2 bits))
        when(isControl) {
          when(!useHeld) { held := 32 }
          when(code === 13) { wasDouble := True }
        }.elsewhen(graphics) {
          when(code(5)) {
            held := code
            heldSet := nextSet
          }
        }.otherwise { held := 32 }
        cellCharacter := renderedCharacter
        cellSet := renderedSet
        cellForeground := colour
        cellBackground := nextBackground
        cellDouble := doubleHeight
        cellConceal := conceal || nextConceal
        cellFlash := flashing && nextFlash
      }
    }
  }
  val normalRaster = scanline.resize(5) @@ ra0.asUInt
  io.glyphRaster := normalRaster.resize(5)
  when(cellDouble) {
    io.glyphRaster := scanline.resize(5) + Mux(secondHalf, U(10, 5 bits), U(0, 5 bits))
  }
  io.character := cellCharacter
  io.glyphSet := cellSet
  io.foreground := cellForeground
  io.background := cellBackground
  io.suppressed := cellConceal || (cellFlash && flashCount < 16) || (secondHalf && !doubleHeight)
  io.attributes := flashCount.asBits ## scanline.asBits ## held.asBits ## hold ## flashing ## conceal ## graphics ## wasDouble ## secondHalf ## doubleHeight ## separated ## background.asBits ## colour.asBits
}

object GenerateTeletextAttributes extends App {
  SpinalConfig(
    targetDirectory = "build/bbc/teletext-attributes",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  ).generateVerilog(TeletextAttributes())
}
