package leet.bbc

import spinal.core._

/** Model B memory and peripherals. Both master clocks share the BBC bus timings.
  * At 96 MHz the video pipeline also has a 12 MHz teletext dot enable.
  */
case class BbcMachine(
    romHex: String = "build/bbc/rom.hex",
    debug: Boolean = true,
    clockMultiplier: Int = 1
) extends Component {
  val io = new Bundle {
    val keys = in Bits (128 bits) // bit(column*8+row), active high
    val warmReset = in Bool () // digital restart request; peripherals keep ticking
    val analogue = in Bits (64 bits) // four unsigned left-aligned ADC channels
    val joystickButtons = in Bits (2 bits) // pressed high; VIA pins are active low
    val debugAddress = in UInt (16 bits)
    val debugData = out Bits (8 bits)
    val cpuEnable = out Bool ()
    val cpuAddress = out UInt (16 bits)
    val cpuWrite = out Bool ()
    val cpuData = out Bits (8 bits)
    val instruction = out Bool ()
    val pc = out UInt (16 bits)
    val accumulator = out Bits (8 bits)
    val indexX = out Bits (8 bits)
    val indexY = out Bits (8 bits)
    val flags = out Bits (8 bits)
    val systemIrq = out Bool ()
    val psgWrite = out Bool ()
    val psgData = out Bits (8 bits)
    val fdcRead = out Bool ()
    val fdcWrite = out Bool ()
    val fdcAddress = out UInt (3 bits)
    val fdcWriteData = out Bits (8 bits)
    val storageAddress = out UInt (18 bits)
    val storageData = in Bits (16 bits)
    val storageWrite = out Bool ()
    val storageWriteData = out Bits (16 bits)
    val pixel = out Bool ()
    val display = out Bool ()
    val rgb = out Bits (3 bits)
    val videoX = out UInt (11 bits)
    val videoY = out UInt (10 bits)
    val vsync = out Bool ()
    val videoVsyncEndpoint = out Bool ()
    val hsync = out Bool ()
    val videoControl = out Bits (8 bits)
    val videoRegisters = out Bits (144 bits)
    val videoPalette = out Bits (64 bits)
    val unsupportedVideo = out Bool ()
    val audioSample = out UInt (8 bits)
    val audioPdm = out Bool ()
  }
  // Peripherals keep ticking while the CPU waits for the slow bus.
  require(clockMultiplier == 1 || clockMultiplier == 3)
  val clock32 =
    if (clockMultiplier == 1) True
    else {
      val divider = Reg(UInt(2 bits)) init (0)
      divider := Mux(divider === 2, U(0, 2 bits), divider + 1)
      divider === 2
    }
  val phase = Reg(UInt(5 bits)) init (0)
  when(clock32) { phase := phase + 1 }
  val tickCpu = clock32 && phase(3 downto 0) === 15
  val tickVia = clock32 && phase === 15
  val cpu = Cpu6502()
  cpu.io.clk := ClockDomain.current.readClockWire
  cpu.io.reset := ClockDomain.current.isResetActive
  cpu.io.warm_reset := io.warmReset
  val address = cpu.io.address
  val decode = BbcAddressDecode()
  decode.io.address := address
  val slow = decode.io.slow
  val delayedSlow = RegInit(False)
  // Every slow access consumes at least two CPU slots and finishes on a 1 MHz
  // edge. A fast access finishes on either half of the 1 MHz clock.
  val cpuEnable = tickCpu && (!slow || (tickVia && delayedSlow))
  when(tickCpu) { delayedSlow := slow && !cpuEnable }
  cpu.io.enable := cpuEnable
  val ram = BbcRam(debug = debug)
  // Match the reference power-on RAM pattern: zero page is 00, the rest FF.
  ram.io.cpuEnable := cpuEnable && address < 0x8000
  ram.io.cpuAddress := address(14 downto 0)
  ram.io.cpuWrite := cpu.io.write_enable
  ram.io.cpuData := cpu.io.write_data
  ram.io.debugAddress := io.debugAddress(14 downto 0)
  val source = scala.io.Source.fromFile(romHex)
  val image =
    try source.getLines().filter(_.nonEmpty).map(s => BigInt(s.trim, 16)).toVector
    finally source.close()
  require(
    image.size == 3 * 16384 && image.forall(v => v >= 0 && v <= 255),
    "Expected verified OS/BASIC/DFS ROMs"
  )
  val rom = Mem(Bits(8 bits), image.map(v => B(v, 8 bits)))
  val romSelect = Reg(UInt(4 bits)) init (0)
  val romAddress = UInt(16 bits)
  romAddress := address(13 downto 0).resize(16)
  when(address < 0xc000) {
    romAddress := address(13 downto 0)
      .resize(16) + Mux(romSelect === 15, U(16384, 16 bits), U(32768, 16 bits))
  }
  val ramData = ram.io.cpuReadData
  val romData = rom.readSync(romAddress, enable = cpuEnable)
  val romPresent = RegNextWhen(address >= 0xc000 || romSelect >= 14, cpuEnable) init (True)
  // An asynchronous observation port prevents block-RAM inference even when
  // an outer wrapper drops its output. Omit it during elaboration for hardware.
  if (debug) {
    val debugRomAddress = UInt(16 bits)
    debugRomAddress := io.debugAddress(13 downto 0).resize(16)
    when(io.debugAddress < 0xc000) {
      debugRomAddress := io.debugAddress(13 downto 0).resize(16) + Mux(
        romSelect === 15,
        U(16384, 16 bits),
        U(32768, 16 bits)
      )
    }
    io.debugData := Mux(
      io.debugAddress < 0x8000,
      ram.io.debugData,
      Mux(
        io.debugAddress >= 0xfc00 && io.debugAddress < 0xff00,
        B(255, 8 bits),
        Mux(
          io.debugAddress >= 0xc000 || romSelect >= 14,
          rom.readAsync(debugRomAddress),
          B(0, 8 bits)
        )
      )
    )
  } else { io.debugData := 0 }
  // Register strobes follow accepted CPU edges; video fetches run independently.
  val sys = Via6522()
  val user = Via6522()
  val adc = Adc7002()
  adc.io.tick := tickVia
  adc.io.address := address(1 downto 0)
  adc.io.data := cpu.io.write_data
  adc.io.channels := io.analogue
  val adcSelected = decode.io.adc
  adc.io.read := cpuEnable && !cpu.io.write_enable && adcSelected
  adc.io.write := cpuEnable && cpu.io.write_enable && adcSelected
  val sysSelected = decode.io.systemVia
  val userSelected = decode.io.userVia
  val read = cpuEnable && !cpu.io.write_enable
  val write = cpuEnable && cpu.io.write_enable
  val crtcSelected = decode.io.crtc
  val video = BbcVideo(clockMultiplier)
  video.io.crtcWrite := write && crtcSelected
  video.io.lightPenPulse := sys.io.cb2Rise
  video.io.ulaWrite := write && decode.io.ula
  video.io.address := address(0)
  video.io.data := cpu.io.write_data
  ram.io.videoEnable := video.io.memoryRead
  ram.io.videoAddress := video.io.memoryAddress
  video.io.memoryData := ram.io.videoReadData
  io.pixel := video.io.pixel
  io.display := video.io.display
  io.rgb := video.io.rgb
  io.videoX := video.io.x
  io.videoY := video.io.y
  io.vsync := video.io.vsync
  io.hsync := video.io.hsync
  io.videoVsyncEndpoint := video.io.busVsync
  io.videoControl := video.io.control
  io.videoRegisters := video.io.registers
  io.videoPalette := video.io.palette
  io.unsupportedVideo := video.io.unsupported
  for ((via, selected) <- Seq((sys, sysSelected), (user, userSelected))) {
    via.io.tick := tickVia
    via.io.phi2 := !phase(4)
    via.io.address := address(3 downto 0)
    via.io.read := read && selected
    via.io.write := write && selected
    via.io.data := cpu.io.write_data
    // Driven outputs return through the input buffer too; only an undriven
    // external connector uses this fixture's inactive input level.
    via.io.cb2 := Mux(via.io.cb2Output, via.io.cb2Pin, False)
  }
  sys.io.portBIn := B(255, 8 bits) ^ (io.joystickButtons.resize(8) |<< 4)
  sys.io.cb1 := Mux(sys.io.cb1Output, sys.io.cb1Pin, adc.io.eocBusN)
  // A collapsed ADC pulse belongs to the external input bank. It must not
  // bypass the pin mux while the VIA drives its internal serial clock.
  sys.io.cb1Pulse := adc.io.eocPulse && !sys.io.cb1Output
  user.io.portBIn := B(255, 8 bits)
  user.io.cb1 := user.io.cb1Output && user.io.cb1Pin
  user.io.cb1Pulse := False
  user.io.ca1 := False
  user.io.ca2 := False
  user.io.portAIn := B(255, 8 bits)
  user.io.ca2AfterWrite := False
  val systemPins = BbcSystemPins()
  systemPins.io.keys := io.keys
  systemPins.io.driveA := sys.io.driveA
  systemPins.io.busDriveA := sys.io.busDriveA
  systemPins.io.busPortB := sys.io.busPortB
  video.io.scroll := systemPins.io.scroll
  sys.io.portAIn := systemPins.io.portAIn
  sys.io.ca2 := systemPins.io.busCa2
  sys.io.ca2AfterWrite := sys.io.write && systemPins.io.ca2 =/= systemPins.io.busCa2
  sys.io.ca1 := video.io.busVsync
  val serial = BbcSerial()
  serial.io.tick := tickCpu
  serial.io.aciaRead := read && decode.io.acia
  serial.io.aciaWrite := write && decode.io.acia
  serial.io.serialRead := read && decode.io.serial
  serial.io.serialWrite := write && decode.io.serial
  serial.io.address := address(0)
  serial.io.data := cpu.io.write_data
  cpu.io.irq := sys.io.irq || user.io.irq || serial.io.irq
  val fdc =
    if (clockMultiplier == 1) Intel8271()
    else {
      val area = new ClockEnableArea(clock32) { val device = Intel8271() }
      area.device
    }
  cpu.io.nmi := fdc.io.nmi
  cpu.io.nmi_event := fdc.io.nmiEvent && clock32
  fdc.io.warmReset := io.warmReset
  io.systemIrq := sys.io.irq
  val audio = BbcAudio(resetPending = true)
  audio.io.clock32 := clock32
  audio.io.enabled := systemPins.io.soundEnabled
  audio.io.data := systemPins.io.soundData
  val portAddress = address(3 downto 0)
  audio.io.touch := write && sysSelected &&
    (portAddress === 0 || portAddress === 1 || portAddress === 2 || portAddress === 3 || portAddress === 15)
  io.psgWrite := audio.io.write
  io.psgData := audio.io.acceptedData
  io.audioSample := audio.io.sample
  io.audioPdm := audio.io.pdm
  val fdcSelected = decode.io.fdc
  io.fdcRead := read && fdcSelected
  io.fdcWrite := write && fdcSelected
  io.fdcAddress := address(2 downto 0)
  io.fdcWriteData := cpu.io.write_data
  fdc.io.read := io.fdcRead
  fdc.io.write := io.fdcWrite
  fdc.io.address := io.fdcAddress
  fdc.io.data := cpu.io.write_data
  fdc.io.storageData := io.storageData
  io.storageAddress := fdc.io.storageAddress
  io.storageWrite := fdc.io.storageWrite && clock32
  io.storageWriteData := fdc.io.storageWriteData
  when(write && decode.io.romSelect) {
    romSelect := cpu.io.write_data(3 downto 0).asUInt
  }
  // Register reads are captured with the same acceptance as RAM/ROM.
  val peripheral = Bits(8 bits)
  peripheral := decode.io.unimplemented
  when(decode.io.acia) { peripheral := serial.io.result }
  when(decode.io.serial) { peripheral := B(0, 8 bits) }
  when(crtcSelected) { peripheral := video.io.result }
  when(sysSelected) { peripheral := sys.io.result }
  when(userSelected) { peripheral := user.io.result }
  when(fdcSelected) { peripheral := fdc.io.result }
  when(adcSelected) { peripheral := adc.io.result }
  val peripheralData = RegNextWhen(peripheral, cpuEnable) init (255)
  val memorySelect = Reg(UInt(2 bits)) init (0)
  when(cpuEnable) {
    memorySelect := Mux(
      address < 0x8000,
      U(0, 2 bits),
      Mux(address >= 0xfc00 && address < 0xff00, U(2, 2 bits), U(1, 2 bits))
    )
  }
  cpu.io.read_data := Mux(
    memorySelect === 0,
    ramData,
    Mux(memorySelect === 1, Mux(romPresent, romData, B(0, 8 bits)), peripheralData)
  )
  io.cpuEnable := cpuEnable
  io.cpuAddress := address
  io.cpuWrite := cpu.io.write_enable
  io.cpuData := cpu.io.write_data
  io.instruction := cpu.io.instruction
  io.pc := cpu.io.pc
  io.accumulator := cpu.io.accumulator
  io.indexX := cpu.io.index_x
  io.indexY := cpu.io.index_y
  io.flags := cpu.io.flags
}

object Generate extends App {
  SpinalConfig(
    targetDirectory = "build/bbc",
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )
    .generateVerilog(BbcMachine())
}
