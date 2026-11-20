package leet.soc

import spinal.core._
import leet.common.RenderConfig
import leet.render.Renderer
import leet.render.elite.{EliteRenderRegisters, EliteRenderMap}
import leet.audio.Psg

/** Simplified-demo SoC with PicoRV32 firmware RAM and memory-mapped rendering.
  *
  * RAM starts at 0x0000_0000; peripherals start at 0x1000_0000. Firmware register offsets and
  * control bits are defined in fw/mmio.h. EliteRenderMap defines the released-code kernel register
  * window, whose operands use original signed-magnitude bytes rather than Q8.8.
  *
  * The framebuffer read port returns data one clock after the address. frameReady remains set until
  * frameAck; the firmware waits for this acknowledgement before beginning another frame.
  * BoardInputs supplies synchronized controls, and InputCapture retains short button presses across
  * rendering until the firmware reads them.
  */
case class Soc(
    ramWords: Int = 8192,
    rcfg: RenderConfig = RenderConfig(screenW = 128, screenH = 128, viewH = 96, colorBits = 1),
    firmwareHex: String = "build/fw/firmware.hex",
    psgClockHz: Int = 0
) extends Component {
  val fbAddrW = log2Up(rcfg.pixels)

  val io = new Bundle {
    val dbg = out Bits (32 bits)
    val trap = out Bool ()
    val fbRdAddr = in UInt (fbAddrW bits)
    val fbRdData = out UInt (rcfg.colorBits bits)
    val frameReady = out Bool ()
    val frameAck = in Bool ()
    val renderBusy = out Bool ()
    val input = in Bits (32 bits)
    val input2 = in Bits (32 bits) // second button word (missiles/ECM/bomb/...)
    val audioPdm = out Bool () // 1-bit sigma-delta audio (PSG) for a board RC DAC
    val audioSample = out UInt (8 bits) // mixed PSG level (testbench / soft DAC)
  }

  // Hold the CPU and peripheral domain in reset for 64 input clock edges.
  // The outer-domain counter also restarts when the integration reset is asserted.
  val por = Reg(UInt(7 bits)) init (0)
  when(por =/= 64) { por := por + 1 }
  val porDone = por === 64
  val porActive = !porDone

  val cpu = new PicoRV32(progReset = 0)
  cpu.io.clk := ClockDomain.current.readClockWire
  cpu.io.resetn := porDone
  cpu.io.irq := 0
  cpu.io.pcpi_wr := False
  cpu.io.pcpi_rd := 0
  cpu.io.pcpi_wait := False
  cpu.io.pcpi_ready := False

  val coreCd = ClockDomain(
    clock = ClockDomain.current.readClockWire,
    reset = porActive,
    config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
  )
  // POR changes reset behavior, not the clock. Declare the shared clock
  // relationship so integration ports are checked as synchronous transfers.
  coreCd.setSynchronousWith(ClockDomain.current)

  val core = new ClockingArea(coreCd) {
    val ram = Mem(Bits(32 bits), Soc.loadHex(firmwareHex, ramWords).map(v => B(v, 32 bits)))
    val render = Renderer(rcfg)
    val psg = Psg(coreClockHz = psgClockHz)
    val elite = EliteRenderRegisters()

    // --- bus decode ---
    val valid = cpu.io.mem_valid
    val addr = cpu.io.mem_addr
    val wstrb = cpu.io.mem_wstrb
    val wdata = cpu.io.mem_wdata
    val isRam = addr(28, 4 bits) === 0
    val isMmio = addr(28, 4 bits) === 1
    val wordAddr = addr(2, log2Up(ramWords) bits)
    val mmioSel = addr(2, 6 bits)

    val ready = RegInit(False)
    ready := valid && !ready
    cpu.io.mem_ready := ready
    val doWrite = valid && !ready && (wstrb =/= 0)
    val mmioWr = doWrite && isMmio
    val eliteSelected = mmioSel >= EliteRenderMap.Base / 4 && mmioSel <= EliteRenderMap.Last / 4
    elite.io.write := mmioWr && eliteSelected && wstrb === 15
    elite.io.address := (mmioSel - EliteRenderMap.Base / 4).resize(elite.io.address.getWidth)
    elite.io.data := wdata

    ram.write(wordAddr, wdata, enable = doWrite && isRam, mask = wstrb)
    val ramRd = ram.readSync(wordAddr)

    // --- MMIO state ---
    val dbgReg = Reg(Bits(32 bits)) init (0)
    val mat = Vec(Reg(SInt(16 bits)) init (0), 9)
    val tvec = Vec(Reg(SInt(16 bits)) init (0), 3)
    val lineP0 = Reg(Bits(32 bits)) init (0)
    val lineP1 = Reg(Bits(32 bits)) init (0)
    val lineCol = Reg(UInt(rcfg.colorBits bits)) init (0)
    val circleCtr = Reg(Bits(32 bits)) init (0)
    val circleRad = Reg(UInt(10 bits)) init (0)
    val circleCol = Reg(UInt(rcfg.colorBits bits)) init (0)
    val modelReg = Reg(UInt(8 bits)) init (0) // selected ship blueprint (modelIdx)
    val doneLatch = RegInit(False)
    val frameReady = RegInit(False)
    // BoardInputs supplies synchronized/debounced controls. Keep short button
    // presses pending across long render operations until the CPU reads them.
    val inputs = InputCapture()
    inputs.io.input := io.input
    inputs.io.input2 := io.input2
    val readAccepted = valid && ready && wstrb === 0 && isMmio
    inputs.io.consume := readAccepted && mmioSel === 3
    inputs.io.consume2 := readAccepted && mmioSel === 22
    val inputReg = inputs.io.word
    val inputReg2 = inputs.io.word2

    val startPulse = mmioWr && (mmioSel === 1) && wdata(0)
    val startLinePulse = mmioWr && (mmioSel === 1) && wdata(2)
    val startCirclePulse = mmioWr && (mmioSel === 1) && wdata(3)
    render.io.start := startPulse
    render.io.clearFirst := wdata(1) // valid while startPulse is high (same control write)
    render.io.modelIdx := modelReg.resize(render.io.modelIdx.getWidth bits)
    render.io.m := mat
    render.io.t.x := tvec(0)
    render.io.t.y := tvec(1)
    render.io.t.z := tvec(2)
    render.io.fbRdAddr := io.fbRdAddr
    render.io.lineStart := startLinePulse
    render.io.lineP0.x := lineP0(15 downto 0).asSInt.resize(render.io.lineP0.x.getWidth bits)
    render.io.lineP0.y := lineP0(31 downto 16).asSInt.resize(render.io.lineP0.y.getWidth bits)
    render.io.lineP1.x := lineP1(15 downto 0).asSInt.resize(render.io.lineP1.x.getWidth bits)
    render.io.lineP1.y := lineP1(31 downto 16).asSInt.resize(render.io.lineP1.y.getWidth bits)
    render.io.lineColor := lineCol
    render.io.circleStart := startCirclePulse
    render.io.circleCenter.x := circleCtr(15 downto 0).asSInt
      .resize(render.io.circleCenter.x.getWidth bits)
    render.io.circleCenter.y := circleCtr(31 downto 16).asSInt
      .resize(render.io.circleCenter.y.getWidth bits)
    render.io.circleRadius := circleRad
    render.io.circleColor := circleCol

    // PSG sound: a byte written to MMIO 0x5C is pushed to the SN76489-style chip.
    psg.io.write := mmioWr && (mmioSel === 23)
    psg.io.data := wdata(7 downto 0)

    when(render.io.done) { doneLatch := True }
    when(startPulse) { doneLatch := False }
    when(mmioWr && (mmioSel === 2) && wdata(0)) { frameReady := True }
    when(io.frameAck) { frameReady := False }

    when(mmioWr) {
      switch(mmioSel) {
        is(0) { dbgReg := wdata }
        // matrix m0..m8 at word offsets 4..12 (0x10..0x30)
        for (i <- 0 until 9) is(4 + i) { mat(i) := wdata(15 downto 0).asSInt }
        // line draw operands at word offsets 13..15 (0x34..0x3C)
        is(13) { lineP0 := wdata }
        is(14) { lineP1 := wdata }
        is(15) { lineCol := wdata(rcfg.colorBits - 1 downto 0).asUInt }
        // translation at word offsets 16..18 (0x40..0x48)
        for (i <- 0 until 3) is(16 + i) { tvec(i) := wdata(15 downto 0).asSInt }
        // circle draw operands at word offsets 19..21 (0x4C..0x54)
        is(19) { circleCtr := wdata }
        is(20) { circleRad := wdata(9 downto 0).asUInt }
        is(21) { circleCol := wdata(rcfg.colorBits - 1 downto 0).asUInt }
        // ship blueprint select at word offset 24 (0x60)
        is(24) { modelReg := wdata(7 downto 0).asUInt }
      }
    }

    val mmioRd = Bits(32 bits)
    mmioRd := 0
    switch(mmioSel) {
      is(0) { mmioRd := dbgReg }
      is(1) { mmioRd := (B(0, 30 bits) ## doneLatch ## render.io.busy) }
      is(2) { mmioRd := frameReady.asBits.resize(32 bits) }
      is(3) { mmioRd := inputReg }
      is(22) { mmioRd := inputReg2 } // 0x58 INPUT2
    }
    when(eliteSelected) { mmioRd := elite.io.read }

    cpu.io.mem_rdata := isRam ? ramRd | mmioRd

    io.dbg := dbgReg
    io.trap := cpu.io.trap
    io.fbRdData := render.io.fbRdData
    io.frameReady := frameReady
    io.renderBusy := render.io.busy
    io.audioPdm := psg.io.pdm
    io.audioSample := psg.io.sample
  } // end ClockingArea(coreCd)
}

object Soc {

  /** Read a hex-word-per-line firmware image into `words` BigInts (zero-padded). */
  def loadHex(path: String, words: Int): Seq[BigInt] = {
    val f = new java.io.File(path)
    require(f.isFile, s"Firmware image missing: $path; run make fw before elaboration")
    val src = scala.io.Source.fromFile(f)
    val content =
      try src.getLines().map(_.trim).filter(_.nonEmpty).map(s => BigInt(s, 16)).toVector
      finally src.close()
    require(content.nonEmpty, s"Firmware image is empty: $path")
    require(content.size <= words, s"Firmware image exceeds RAM: ${content.size} words > $words")
    require(
      content.forall(v => v >= 0 && v < (BigInt(1) << 32)),
      s"Firmware word outside 32 bits: $path"
    )
    content ++ Vector.fill(words - content.size)(BigInt(0))
  }
}
