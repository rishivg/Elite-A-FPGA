package leet.bbc

import spinal.core._

/** Model B non-DMA 8271 host and FM byte processor. The external store contains 80 tracks of 3,125
  * clock/data words; marker, header, payload and CRC damage remain in that medium. Serial service
  * follows the rotating head at 64 us per word. BBC DMA/SCAN reports its first-byte error. Track
  * formatting remains unsupported.
  */
case class Intel8271(masterClockHz: Int = 32000000) extends Component {
  require(masterClockHz >= 1000000 && masterClockHz <= 32000000 && masterClockHz % 1000000 == 0)
  private val clocksPerUs = masterClockHz / 1000000
  private val sourceTickClocks = math.max(1, masterClockHz / 2000000)
  val io = new Bundle {
    val read = in Bool ()
    val write = in Bool ()
    val warmReset = in Bool ()
    val address = in UInt (3 bits)
    val data = in Bits (8 bits)
    val result = out Bits (8 bits)
    val nmi = out Bool ()
    val nmiEvent = out Bool ()
    val storageAddress = out UInt (18 bits)
    val storageData = in Bits (16 bits)
    val storageWrite = out Bool ()
    val storageWriteData = out Bits (16 bits)
  }
  object Phase extends SpinalEnum {
    val Idle, Parameters, Specify, Dispatch, Seek, Delay, IdWaitIndex, Search, Transfer, SectorEnd,
        ReadSpecial, IdCrcWait =
      newElement()
  }
  val phase = Reg(Phase()) init (Phase.Idle)
  // Dispatch, millisecond and index callbacks precede this word. Their
  // resulting view is read by the byte processor before host acceptance.
  val preBytePhase = Phase()
  preBytePhase := phase
  val preByteStopped = Bool()
  preByteStopped := False
  // Millisecond work and the serial byte program continue independently.
  // A replacement seek owns its timer without discarding an old byte request.
  object Mechanical extends SpinalEnum { val Idle, Seek, Delay = newElement() }
  val mechanicalPhase = Reg(Mechanical()) init (Mechanical.Idle)
  val mechanicalDelay = Reg(UInt(24 bits)) init (0)
  val mechanicalOp = Reg(UInt(4 bits)) init (0)
  val dispatchPending = RegInit(False)
  dispatchPending := False
  val command = Reg(Bits(8 bits)) init (0)
  val hostParameter = Reg(Bits(8 bits)) init (0)
  val driveInLatched = Reg(Bits(8 bits)) init (0)
  val driveInCopy = Reg(Bits(8 bits)) init (0)
  val eventDriveInCopy = Bits(8 bits)
  val hostDriveInCopy = Bits(8 bits)
  val preByteDriveInCopy = Bits(8 bits)
  eventDriveInCopy := driveInCopy
  hostDriveInCopy := eventDriveInCopy
  preByteDriveInCopy := driveInCopy
  val parameters = Vec.fill(5)(Reg(Bits(8 bits)) init (0))
  // Registers 00..1f are internal RAM. The upper half aliases eight MMIO
  // slots; those device registers must not become extra RAM cells.
  // Frequently used mechanical registers have mirrors on the same write port.
  val special = Mem(Bits(8 bits), 32)
  special.init(Seq.fill(32)(B(0, 8 bits)))
  val specialWrite = Bool()
  specialWrite := False
  val specialAddress = UInt(5 bits)
  specialAddress := 0
  val specialData = Bits(8 bits)
  specialData := 0
  val specialValid = Reg(Bits(32 bits)) init (0)
  // RAM cells do not reset. A validity bitmap restores the original bank's
  // immediate zero reads on board reset without clearing 32 cells serially.
  // The 8271's register-2 reset intentionally leaves this bank intact.
  val bankWrite = specialWrite && !ClockDomain.current.isResetActive
  special.write(specialAddress, specialData, bankWrite)
  when(bankWrite) { specialValid(specialAddress) := True }
  val stepRate = Reg(Bits(8 bits)) init (0)
  val settleRate = Reg(Bits(8 bits)) init (0)
  val headRate = Reg(Bits(8 bits)) init (0)
  val currentTrack0 = Reg(Bits(8 bits)) init (0)
  val currentTrack1 = Reg(Bits(8 bits)) init (0)
  val badTrack00 = Reg(UInt(8 bits)) init (0)
  val badTrack01 = Reg(UInt(8 bits)) init (0)
  val badTrack10 = Reg(UInt(8 bits)) init (0)
  val badTrack11 = Reg(UInt(8 bits)) init (0)
  val mode = Reg(Bits(8 bits)) init (0)
  // Preserve the complete shared register across clock-enable elaborations.
  // Its command-full bit has a distinct host-write enable.
  mode.addAttribute("keep")
  val byteCounterLow = Reg(UInt(8 bits)) init (0)
  val byteCounterHigh = Reg(UInt(8 bits)) init (0)
  val register0 = Reg(UInt(8 bits)) init (0)
  val dataMarker = Reg(Bits(8 bits)) init (0)
  val headerPointer = Reg(UInt(8 bits)) init (0)
  val idSize = Reg(Bits(8 bits)) init (0)
  val idSector = Reg(Bits(8 bits)) init (0)
  val idSide = Reg(Bits(8 bits)) init (0)
  val idTrack = Reg(Bits(8 bits)) init (0)
  val preByteIdTrack = Bits(8 bits)
  preByteIdTrack := idTrack
  val indexCount = Reg(UInt(8 bits)) init (0)
  val parameterCount = Reg(UInt(8 bits)) init (0)
  def mirrorSpecial(address: UInt, value: Bits, counters: Boolean = true): Unit = {
    switch(address) {
      if (counters) {
        is(0x00) { register0 := value.asUInt }
        is(0x01) { parameterCount := value.asUInt }
      }
      is(0x02) { dataMarker := value }
      is(0x08) { headerPointer := value.asUInt }
      is(0x09) { idSize := value }
      is(0x0a) { idSector := value }
      is(0x0b) { idSide := value }
      is(0x0c) { idTrack := value }
      is(0x1c) { indexCount := value.asUInt }
      is(0x03) { parameters(4) := value }
      is(0x04) { parameters(3) := value }
      is(0x05) { parameters(2) := value }
      is(0x06) { parameters(1) := value }
      is(0x07) { parameters(0) := value }
      is(0x1e) { hostParameter := value }
      is(0x1f) { command := value }
      is(0x0d) { stepRate := value }
      is(0x0e) { settleRate := value }
      is(0x0f) { headRate := value }
      is(0x10) { badTrack00 := value.asUInt }
      is(0x11) { badTrack01 := value.asUInt }
      is(0x12) { currentTrack0 := value }
      is(0x13) { byteCounterLow := value.asUInt }
      is(0x14) { byteCounterHigh := value.asUInt }
      is(0x17) { mode := value }
      is(0x18) { badTrack10 := value.asUInt }
      is(0x19) { badTrack11 := value.asUInt }
      is(0x1a) { currentTrack1 := value }
    }
  }
  when(specialWrite) { mirrorSpecial(specialAddress, specialData) }
  when(specialWrite && specialAddress === 0x15) { eventDriveInCopy := specialData }
  def writeSpecial(address: UInt, data: Bits): Unit = {
    when(!address(5)) {
      specialWrite := True
      specialAddress := address(4 downto 0)
      specialData := data
    }
  }
  val busy = RegInit(False)
  // The logical track latch changes before a seek finishes. Retain the actual
  // head position across controller aborts so subsequent reads and TRK0 calibration
  // follow the completed step pulses rather than the interrupted target.
  val physicalTrack = Reg(UInt(8 bits)) init (0)
  val mediaPhysicalTrack = UInt(8 bits)
  mediaPhysicalTrack := physicalTrack
  // Drive 1 has its own actuator even when it has no medium. Its private
  // TRK0 condition determines calibration timing before READY is checked.
  val physicalTrack1 = Reg(UInt(8 bits)) init (0)
  val calibrating = idTrack === B(0, 8 bits)
  // R1 is also the command parameter counter. A partial command can change
  // the retry budget while the old byte processor continues running.
  val headerIndex = Reg(UInt(4 bits)) init (0)
  val headerSeen = RegInit(False)
  val preByteHeaderSeen = Bool()
  preByteHeaderSeen := headerSeen
  val headerRecovering = RegInit(False)
  val preByteHeaderRecovering = Bool()
  preByteHeaderRecovering := headerRecovering
  // Original Number ++ stops changing at 2^53. Keep that exact boundary
  // while matching alternating clock/data bits across arbitrary word edges.
  val syncAge = Reg(UInt(54 bits)) init (0)
  val syncShift = Reg(Bits(16 bits)) init (0)
  val syncCount = Reg(UInt(5 bits)) init (0)
  val syncChecking = RegInit(False)
  val preSyncAge = UInt(54 bits)
  val preSyncShift = Bits(16 bits)
  val preSyncCount = UInt(5 bits)
  val preSyncChecking = Bool()
  preSyncAge := syncAge
  preSyncShift := syncShift
  preSyncCount := syncCount
  preSyncChecking := syncChecking
  // One index callback owns R1c: none, search timeout, head unload, or
  // READ ID index synchronisation. Register programming never cancels it.
  val indexCallback = Reg(UInt(2 bits)) init (0)
  val searchTimed = indexCallback === 1
  // Millisecond callbacks run before a coincident drive pulse. The pulse
  // dispatches the resulting callback, including a newly armed unload.
  val preIndexCallback = UInt(2 bits)
  val preIndexCount = UInt(8 bits)
  val preIndexSpinning = Bool()
  preIndexCallback := indexCallback
  preIndexCount := indexCount
  def stepHead(up: Bool): Unit = {
    when(drivePresent) {
      when(up && physicalTrack < 83) { physicalTrack := physicalTrack + 1 }
        .elsewhen(!up && physicalTrack =/= 0) { physicalTrack := physicalTrack - 1 }
    }
    when(driveOut(7 downto 6) === B"10") {
      when(up && physicalTrack1 < 83) { physicalTrack1 := physicalTrack1 + 1 }
        .elsewhen(!up && physicalTrack1 =/= 0) { physicalTrack1 := physicalTrack1 - 1 }
    }
  }
  val driveOut = Reg(Bits(8 bits)) init (0)
  val preByteGate = Bool()
  preByteGate := driveOut(0)
  val selectedDrive = driveOut(7) ^ driveOut(6)
  val selectedPhysicalTrack = Mux(driveOut(7), physicalTrack1, physicalTrack)
  val calibrationAtZero = selectedDrive && selectedPhysicalTrack === 0
  val mmioClocks = Reg(Bits(8 bits)) init (0)
  val mmioData = Reg(Bits(8 bits)) init (0)
  val rephaseByteService = Bool()
  rephaseByteService := False
  def writeRegister(address: UInt, data: Bits, acceptedHost: Boolean = false): Unit = {
    writeSpecial(address, data)
    when(!address(5) && address(4 downto 0) === U(0x15, 5 bits)) {
      if (acceptedHost) { hostDriveInCopy := data }
      else { eventDriveInCopy := data; preByteDriveInCopy := data }
    }
    when(!address(5) && address(4 downto 0) === U(0x16, 5 bits)) {
      if (acceptedHost) { hostResult := data }
      else { eventResult := data }
    }
    when(address(5)) {
      switch(address(2 downto 0)) {
        is(3) {
          when(spinning) {
            when(data(3) && data(7 downto 6) === driveOut(7 downto 6)) {
              rephaseByteService := True
              // A drive-output write restarts the reference byte timer even
              // when LOAD remains high. Preserve its pending physical cell.
              val cell = U(64 * clocksPerUs, 23 bits)
              val rounded = ((rotation + cell - 1) / cell) * cell
              val edge = Mux(
                rounded === 0 || rounded >= masterClockHz / 5,
                U(masterClockHz / 5, 23 bits),
                rounded.resize(23)
              )
              val dispatchOffset = if (acceptedHost) 0 else 1
              rotation := edge - U(masterClockHz / 2000000 - dispatchOffset, 23 bits)
            }.otherwise { stopRotation() }
          }
          driveOut := data
        }
        is(4) { mmioClocks := data }
        is(5) { mmioData := data }
      }
    }
  }
  val rotation = Reg(UInt(23 bits)) init (0)
  // A stopped original drive retains the next accepted FM cell. Preserve
  // that cell when public drive-output writes stop or deselect its motor.
  def stopRotation(): Unit = {
    val nextCell = rotation + U(64 * clocksPerUs - 1, 23 bits)
    val rounded = (nextCell / U(64 * clocksPerUs, 23 bits)) * (64 * clocksPerUs)
    rotation := Mux(rounded >= masterClockHz / 5, U(0, 23 bits), rounded.resize(23))
  }
  val readyTimeout = Reg(UInt(24 bits)) init (0)
  val readyPulses = Reg(UInt(2 bits)) init (0)
  val previousSelection = RegNext(driveOut(7 downto 6)) init (0)
  // The external sector interface supplies drive 0 only. An absent drive
  // must never read or overwrite that image through another selection.
  val drivePresent = driveOut(7 downto 6) === B"01"
  // The released SSD has headers on the lower side of tracks 0..79 only.
  // The upper side and tracks 80..83 still rotate, but contain no headers.
  val headerPresent = drivePresent && !driveOut(5) && mediaPhysicalTrack < 80
  val spinning = driveOut(3) && drivePresent
  preIndexSpinning := spinning
  val selectedMotor = driveOut(3) && selectedDrive
  // Model B's READY is an index-pulse latch, not a permanently asserted pin.
  // At 300 rpm, the 8271 unload delay counts 200 ms revolutions. The external
  // ready latch survives unloading for 8192 periods of its 31.25 kHz clock.
  val index = rotation === (masterClockHz / 5 - 1)
  when(readyTimeout =/= 0) {
    readyTimeout := readyTimeout - 1
    when(readyTimeout === 1) { readyPulses := 0 }
  }
  when(spinning) {
    rotation := Mux(index, U(0, 23 bits), rotation + 1)
    when(index) {
      // IC83..86 latch READY only after two pulses within the 262 ms timeout.
      readyTimeout := masterClockHz / 31250 * 8192
      readyPulses := Mux(
        readyTimeout === 0,
        U(1, 2 bits),
        Mux(readyPulses === 2, U(2, 2 bits), readyPulses + 1)
      )
    }
  }
  when(driveOut(7 downto 6) =/= previousSelection) {
    readyTimeout := 0
    readyPulses := 0
  }
  val driveIn = Bits(8 bits)
  driveIn := B(0x81, 8 bits)
  driveIn(2) := readyTimeout =/= 0 && readyPulses === 2 && drivePresent
  driveIn(1) := selectedMotor && selectedPhysicalTrack === 0
  // The pinned empty-drive model holds INDEX high; it produces no READY edges.
  driveIn(4) := selectedMotor && (driveOut(7) || rotation < masterClockHz / 250)
  // READY is also retained by the controller's drive-status register. A
  // newly high pin is confirmed by READ DRIVE STATUS before data commands
  // use it; low observations clear the retained bit immediately.
  val latchedReady = RegInit(False)
  val acceptingParameters = RegInit(False)
  val specifying = RegInit(False)
  val dataAccess = io.address(2)
  val hostReset = io.write && io.address === 2 && io.data === B(1, 8 bits)
  val ready = RegInit(False)
  val interrupt = RegInit(False)
  val request = RegInit(False)
  // Direct status writes can change the NMI bit without driving the CPU pin.
  // Peripheral IRQ events and host acknowledgements update the pin latch.
  val nmiLevel = RegInit(False)
  val result = Reg(Bits(8 bits)) init (0)
  val data = Reg(Bits(8 bits)) init (0)
  // Peripheral events precede the accepted host access. Readback sees the
  // event; CPU NMI polling sees the event followed by its acknowledgement.
  val eventBusy = Bool()
  eventBusy := busy
  val eventReady = Bool()
  eventReady := ready
  val eventInterrupt = Bool()
  eventInterrupt := interrupt
  val eventRequest = Bool()
  eventRequest := request
  val eventResult = Bits(8 bits)
  eventResult := result
  val eventData = Bits(8 bits)
  eventData := data
  val eventAcceptingParameters = Bool()
  eventAcceptingParameters := acceptingParameters
  val eventSpecifying = Bool()
  eventSpecifying := specifying || phase === Phase.Specify
  val eventRegister0 = UInt(8 bits)
  eventRegister0 := register0
  val eventParameterCount = UInt(8 bits)
  eventParameterCount := parameterCount
  val eventCounterLow = UInt(8 bits)
  val eventCounterHigh = UInt(8 bits)
  eventCounterLow := byteCounterLow
  eventCounterHigh := byteCounterHigh
  val hostBusy = Bool()
  hostBusy := eventBusy
  val hostReady = Bool()
  hostReady := eventReady
  val hostInterrupt = Bool()
  hostInterrupt := eventInterrupt
  val eventNmi = Bool()
  eventNmi := nmiLevel
  val hostNmi = Bool()
  hostNmi := eventNmi
  val commandNmi = Bool()
  commandNmi := False
  val abortNmi = Bool()
  abortNmi := False
  val serialAbort = Bool()
  serialAbort := False
  // An acknowledged high pin must be sampled low before another callback.
  // A callback during this gap remains in hostNmi and appears when it ends.
  val nmiGap = RegInit(False)
  nmiGap := False
  val hostRequest = Bool()
  hostRequest := eventRequest
  val hostResult = Bits(8 bits)
  hostResult := eventResult
  val hostData = Bits(8 bits)
  hostData := eventData
  val position = Reg(UInt(15 bits)) init (0)
  val sectorAddress = Reg(UInt(18 bits)) init (0)
  // Per-step timing fits even the largest programmable 510 ms step interval.
  // Count steps separately, avoiding a wide steps * interval multiplier.
  val delay = Reg(UInt(24 bits)) init (0)
  val stepsRemaining = idSector.asUInt
  // 2000 = 2048 - 32 - 16. Keeping the constant arithmetic explicit
  // avoids DSP inference for programmable mechanical delays.
  def twoMilliseconds(value: UInt): UInt = {
    val extended = value.resize(24)
    (((extended |<< 11) - (extended |<< 5) - (extended |<< 4)) * clocksPerUs).resize(24)
  }
  val stepDelay =
    Mux(stepRate === 0, U(3000 * clocksPerUs, 24 bits), twoMilliseconds(stepRate.asUInt))
  // Load/settle is chosen when the last step timer fires, not when seeking
  // starts: host programming during a seek affects this next allocation.
  val seekPostDelay = Mux(
    !driveOut(3),
    (twoMilliseconds(headRate(3 downto 0).asUInt) |<< 2),
    twoMilliseconds(settleRate.asUInt)
  )
  // R6 is the live requested sector, including replacement command writes.
  val sector = parameters(1).asUInt
  // Declared SSD track layout: 300 rpm FM, 3125 byte cells/revolution,
  // 16 FF + 6 sync cells before ID, 310 cells between sector headers.
  // Flux errors, nonstandard formatting and spindle acceleration are pending.
  val op = command(5 downto 2).asUInt
  // The retry target constructor is shared by every ordinary dispatch. Each
  // bad-track skip wraps to a byte before the next comparison.
  def translatedTrack(request: UInt, retries: UInt, drive1: Bool): UInt = {
    val requested = request.resize(9) + retries.resize(9)
    val bad0 = Mux(drive1, badTrack10, badTrack00).resize(9)
    val bad1 = Mux(drive1, badTrack11, badTrack01).resize(9)
    val first = Mux(
      requested =/= 0 && bad0 <= requested,
      (requested + 1).resize(8).resize(9),
      requested
    )
    Mux(
      requested =/= 0 && bad1 <= first,
      (first + 1).resize(8).resize(9),
      first
    ).resize(8)
  }
  val retryTarget = translatedTrack(parameters(0).asUInt, parameterCount, driveOut(7))
  val nextRetryCount = (parameterCount + 1).resize(8)
  val nextRetryTarget = translatedTrack(parameters(0).asUInt, nextRetryCount, driveOut(7))
  val specialMmio = Bool()
  specialMmio := parameters(0)(5)
  val readSpecialNow = dispatchPending && op === 15
  val dispatchClocks = driveOut(7 downto 6) ## B(0, 6 bits)
  val specialRead =
    special.readSync(parameters(0)(4 downto 0).asUInt, dispatchPending && op === 15)
  val specialReadValid = RegNextWhen(
    specialValid(parameters(0)(4 downto 0).asUInt),
    dispatchPending && op === 15
  ) init (False)
  // Both logical track mirrors may change on a single-actuator seek. Read
  // their live latches rather than stale RAM copies written through one port.
  val specialReadTrack = RegNextWhen(
    Mux(parameters(0)(3), currentTrack1, currentTrack0),
    dispatchPending && op === 15
  ) init (B(0, 8 bits))
  val specialReadCounter = RegNextWhen(
    Mux(
      parameters(0)(5 downto 0) === B(0, 6 bits),
      register0,
      Mux(parameters(0)(0), byteCounterLow, byteCounterHigh)
    ),
    dispatchPending && op === 15
  ) init (U(0, 8 bits))
  val liveSpecial = Bits(8 bits)
  val liveSpecialValid = Bool()
  liveSpecial := 0
  liveSpecialValid := False
  switch(parameters(0)(5 downto 0)) {
    is(0x01) { liveSpecial := parameterCount.asBits; liveSpecialValid := True }
    for (address <- 3 to 7) {
      is(address) { liveSpecial := parameters(7 - address); liveSpecialValid := True }
    }
    is(0x02) { liveSpecial := dataMarker; liveSpecialValid := True }
    is(0x08) { liveSpecial := headerPointer.asBits; liveSpecialValid := True }
    is(0x09) { liveSpecial := idSize; liveSpecialValid := True }
    is(0x0a) { liveSpecial := idSector; liveSpecialValid := True }
    is(0x0b) { liveSpecial := idSide; liveSpecialValid := True }
    is(0x0c) { liveSpecial := idTrack; liveSpecialValid := True }
    is(0x1c) { liveSpecial := indexCount.asBits; liveSpecialValid := True }
    is(0x15) { liveSpecial := driveInCopy; liveSpecialValid := True }
    is(0x16) { liveSpecial := result; liveSpecialValid := True }
    is(0x17) {
      liveSpecial := (mode & B(0x73, 8 bits)) |
        Mux(busy, B(0x80, 8 bits), B(0, 8 bits)) |
        Mux(interrupt, B(0x08, 8 bits), B(0, 8 bits)) |
        Mux(request, B(0x04, 8 bits), B(0, 8 bits))
      liveSpecialValid := True
    }
    is(0x1b) {
      liveSpecial := driveInLatched; liveSpecialValid := True
    }
    is(0x1d) { liveSpecial := data; liveSpecialValid := True }
    is(0x1e) { liveSpecial := hostParameter; liveSpecialValid := True }
    is(0x1f) { liveSpecial := command & B(0x3c, 8 bits); liveSpecialValid := True }
  }
  val specialReadLive =
    RegNextWhen(liveSpecial, dispatchPending && op === 15) init (B(0, 8 bits))
  val specialReadLiveValid =
    RegNextWhen(liveSpecialValid, dispatchPending && op === 15) init (False)
  // A partial command changes the public command register, but the existing
  // byte processor retains its read/write/ID context until dispatch replaces it.
  val executingOp = Reg(UInt(4 bits)) init (0)
  val preByteOwner = UInt(4 bits)
  preByteOwner := executingOp

  val byteOperation =
    preBytePhase === Phase.Search || preBytePhase === Phase.Transfer || preBytePhase === Phase.SectorEnd || preBytePhase === Phase.IdCrcWait
  val physicalOperation =
    mechanicalPhase =/= Mechanical.Idle || byteOperation || phase === Phase.IdWaitIndex
  val activeOp =
    Mux(byteOperation, preByteOwner, Mux(mechanicalPhase =/= Mechanical.Idle, mechanicalOp, op))
  val isWrite = activeOp === 2 || activeOp === 3
  val isId = activeOp === 6
  val isVerify = activeOp === 7
  val isScan = op === 0 || op === 1
  val readCrc = Reg(Bits(16 bits)) init (B(65535, 16 bits))
  val readCrcHigh = Reg(Bits(8 bits)) init (0)
  val writeCrc = Reg(Bits(16 bits)) init (B(65535, 16 bits))
  val writeCrcStage = Reg(UInt(3 bits)) init (0)
  def addCrc(initial: Bits, byte: Bits): Bits = {
    var value = initial
    for (bit <- 7 to 0 by -1) {
      val shifted = (value |<< 1).resize(16)
      val next = Bits(16 bits)
      next := Mux(value(15) ^ byte(bit), shifted ^ B(0x1021, 16 bits), shifted)
      value = next
    }
    value
  }
  val rawDeletedData = RegInit(False)

  val validDataMarker = io.storageData(15 downto 8) === B(0xc7, 8 bits) &&
    (io.storageData(7 downto 0) === B(0xfb, 8 bits) || io
      .storageData(7 downto 0) === B(0xf8, 8 bits))
  val validDataClocks = io.storageData(15 downto 8) === B(255, 8 bits)
  val readDataPending = RegInit(False)
  val dataSyncZeros = Reg(UInt(2 bits)) init (0)

  val byteIrqs = mode(5 downto 4) === B"11"
  // The index callback precedes the serial-byte callback at this boundary.
  val indexExpires = preIndexCallback === 1 && preIndexSpinning && index && preIndexCount === 1
  val arrivingByte = preBytePhase === Phase.Transfer && delay === 0 && (!request || !byteIrqs) &&
    !readDataPending && !indexExpires
  // Power-of-two master rates use only the counter low bits.
  private val cellClocks = 64 * clocksPerUs
  val cellPosition =
    if ((cellClocks & (cellClocks - 1)) == 0) rotation(log2Up(cellClocks) - 1 downto 0)
    else rotation % U(cellClocks, 23 bits)
  val cellBoundary = preIndexSpinning && cellPosition === (cellClocks - 1)
  // The bit processor copies the host byte on each accepted FM cell.
  // Clock, marker, payload and CRC words share the external physical cell port.
  when(cellBoundary) { mmioData := data }
  val lastCellData = RegNextWhen(io.storageData, cellBoundary) init (B(0, 16 bits))
  // The sector adapter is replaced by complete clock/data cells. Rotation
  // retains the next accepted head cell, including when commands leave a gate open.
  val rawCell = (rotation / U(cellClocks, 23 bits)).resize(12) + 1
  val nextCell = Mux(rawCell === 3125, U(0, 12 bits), rawCell)
  val physicalAddress = (mediaPhysicalTrack.resize(18) * 3125 + nextCell.resize(18)).resize(18)
  io.storageAddress := Mux(mediaPhysicalTrack < 80, physicalAddress, U(0, 18 bits))
  io.storageWrite := drivePresent && physicalTrack < 80 && !driveOut(5) && driveOut(
    0
  ) && preByteGate && cellBoundary
  io.storageWriteData := Mux(dispatchPending, dispatchClocks, mmioClocks) ## mmioData
  val byteDataLoss = byteIrqs && (!mode(0) || isScan || request)
  val gapByte = cellBoundary && preBytePhase === Phase.Transfer && readDataPending && !isId &&
    (dataSyncZeros === 3 || (isWrite && dataSyncZeros === 2) ||
      (!isWrite && !isId && dataSyncZeros === 2 && io.storageData =/= B(
        0xff00,
        16 bits
      ))) && !indexExpires
  val gapByteLoss = gapByte && byteDataLoss
  val lateWrite =
    preBytePhase === Phase.Transfer && delay === 0 && !readDataPending && isWrite && byteIrqs &&
      (request || !mode(0)) && !indexExpires
  val abortMedia =
    (io.warmReset || hostReset || lateWrite || indexExpires) && isWrite && drivePresent &&
      (preBytePhase === Phase.Transfer || preBytePhase === Phase.SectorEnd)
  when(
    (io.warmReset || hostReset || lateWrite || serialAbort) && preByteGate && drivePresent && physicalTrack < 80
  ) {
    io.storageWrite := True
    io.storageWriteData := B(65535, 16 bits)
  }
  // Index expiry lowers the gate before this cell is emitted. It does not
  // write the FF/FF closing word used by reset and late-DMA callbacks.
  when(indexExpires && !io.warmReset) { io.storageWrite := False }
  when(io.write && dataAccess) { hostData := io.data }
  // The CPU's edge latch must see a callback before this same bus access
  // acknowledges it. nmiLevel still commits the post-access hostNmi value.
  io.nmi := (eventNmi || commandNmi) && !io.warmReset && !nmiGap
  io.nmiEvent := abortNmi && !io.warmReset
  io.result := B(255, 8 bits)
  switch(io.address) {
    is(0) {
      io.result := eventBusy ## B"00" ## eventReady ## eventInterrupt ## eventRequest ## B"00"
      when(io.warmReset) { io.result := B"000" ## ready ## B"0000" }
    }
    is(1) { io.result := eventResult }
    is(2) { io.result := eventCounterHigh.asBits }
    is(3) { io.result := eventCounterLow.asBits }
    is(4, 5, 6, 7) { io.result := eventData }
  }
  def finishSimple(
      peripheral: Boolean = true,
      beforeIndex: Boolean = false,
      beforeByte: Boolean = false
  ): Unit = {
    if (beforeByte || beforeIndex) {
      preBytePhase := Phase.Idle
      preByteStopped := True
      when(headRate(7 downto 4) === 0) { preByteGate := False }
    }
    acceptingParameters := False
    specifying := False
    if (peripheral) {
      eventAcceptingParameters := False
      eventSpecifying := False
    }
    eventBusy := False
    phase := Phase.Idle
    mechanicalPhase := Mechanical.Idle
    dispatchPending := False
    mode := mode & B(0xcf, 8 bits)
    indexCallback := 0
    if (beforeIndex) { preIndexCallback := 0 }
    when(headRate(7 downto 4) =/= 0 && headRate(7 downto 4) =/= 15) {
      indexCallback := 2
      indexCount := headRate(7 downto 4).asUInt.resize(8)
      if (beforeIndex) {
        preIndexCallback := 2
        preIndexCount := headRate(7 downto 4).asUInt.resize(8)
      }
    }
    when(headRate(7 downto 4) === 0) {
      driveOut := driveOut & B(0x37, 8 bits)
      if (beforeIndex) { preIndexSpinning := False }
    }
  }
  def finish(code: Bits, beforeIndex: Boolean = false, beforeByte: Boolean = false): Unit = {
    if (beforeByte || beforeIndex) { when(code =/= 0) { preByteGate := False } }
    eventResult := code | result
    eventReady := True
    eventInterrupt := True
    eventNmi := True
    finishSimple(beforeIndex = beforeIndex, beforeByte = beforeByte)
    when(code =/= B(0, 8 bits)) {
      driveOut := driveOut & B(0xf8, 8 bits)
      when(headRate(7 downto 4) === 0) { driveOut := driveOut & B(0x30, 8 bits) }
    }
  }
  def startHeaderSearch(beforeByte: Boolean = false): Unit = {
    syncAge := 0; syncShift := 0; syncCount := 0; syncChecking := False
    if (beforeByte) {
      preSyncAge := 0; preSyncShift := 0; preSyncCount := 0; preSyncChecking := False
    }
    if (beforeByte) {
      preBytePhase := Phase.Search
      preByteHeaderSeen := False
      preByteHeaderRecovering := False
    }
    phase := Phase.Search
    headerPointer := 12
    headerSeen := False
    headerRecovering := False
  }
  when(delay =/= 0) { delay := delay - 1 }
  when(mechanicalDelay =/= 0) { mechanicalDelay := mechanicalDelay - 1 }
  def beginSeek(targetTrack: UInt, dispatchOffset: Int): Unit = {
    val currentTrack = Mux(
      targetTrack === 0,
      U(255, 8 bits),
      Mux(driveOut(7), currentTrack1, currentTrack0).asUInt
    )
    val needsStep = targetTrack =/= currentTrack &&
      !(targetTrack === 0 && calibrationAtZero)
    val distance =
      Mux(targetTrack >= currentTrack, targetTrack - currentTrack, currentTrack - targetTrack)
    val steps = Mux(needsStep, distance, U(0, 8 bits))
    if (dispatchOffset != 0) {
      when(needsStep && drivePresent) {
        when(targetTrack >= currentTrack && physicalTrack < 83) {
          mediaPhysicalTrack := physicalTrack + 1
        }
        when(targetTrack < currentTrack && physicalTrack =/= 0) {
          mediaPhysicalTrack := physicalTrack - 1
        }
      }
    }
    idSide := targetTrack.asBits
    idTrack := targetTrack.asBits
    if (dispatchOffset != 0) { preByteIdTrack := targetTrack.asBits }
    val headTime = (twoMilliseconds(headRate(3 downto 0).asUInt) |<< 2)
    val settleTime = twoMilliseconds(settleRate.asUInt)
    val postSeek = Mux(!driveOut(3), headTime, Mux(steps =/= 0, settleTime, U(0, 24 bits)))
    // R0a is both the remaining seek count and the ID-sector byte. The
    // first pulse is issued now, so it leaves distance-1 pending callbacks.
    when(targetTrack =/= currentTrack) {
      idSector := Mux(needsStep, distance - 1, distance).asBits
    }
    writeSpecial(Mux(driveOut(7), U(0x1a, 6 bits), U(0x12, 6 bits)), targetTrack.asBits)
    // The motor starts when seeking finishes, not at command dispatch.
    driveOut(3) := driveOut(3) || steps === 0
    driveOut(4) := targetTrack >= 43
    when(targetTrack =/= currentTrack) {
      driveOut(2) := targetTrack > currentTrack
      driveOut(1) := False
      when(mode(1)) {
        currentTrack0 := targetTrack.asBits
        currentTrack1 := targetTrack.asBits
      }
    }
    // Schedule millisecond expiry on the drive callback phase of its source
    // tick. Host dispatch starts at the following base edge; serial retries
    // already run on the callback phase and need only expiration accounting.
    when(steps =/= 0) {
      stepHead(targetTrack >= currentTrack)
      mechanicalDelay := stepDelay - (dispatchOffset + 1)
      mechanicalPhase := Mechanical.Seek
    }
      .otherwise {
        if (dispatchOffset == 0) {
          when(postSeek === 0) { completeSeek() }
            .otherwise { mechanicalDelay := postSeek - 1; mechanicalPhase := Mechanical.Delay }
        } else {
          when(postSeek === 0) {
            completeSeek(beforeIndex = true, context = op)
          }.otherwise {
            mechanicalDelay := postSeek - (dispatchOffset + 1)
            mechanicalPhase := Mechanical.Delay
          }
        }
      }
    // Programmable 5.25-inch step, settle and head-load delays. Sector
    // flux and motor acceleration remain separate fidelity gaps.
  }
  when(dispatchPending) {
    when(op === 2 || op === 3) { dataMarker := Mux(op === 3, B(0xf8, 8 bits), B(0xfb, 8 bits)) }
    // The original command dispatcher also uses this MMIO register as its
    // temporary drive-select byte before a command-specific write replaces it.
    mmioClocks := dispatchClocks
    switch(op) {
      is(13) {
        register0 := parameters(0).asUInt
        parameterCount := 3

        specifying := True
      }
      is(14) {
        writeRegister(parameters(0)(5 downto 0).asUInt, parameters(1))
        eventBusy := False
      }
      is(15) { completeReadSpecial(asynchronous = true) }
      is(11) {
        eventDriveInCopy := driveIn
        preByteDriveInCopy := driveIn
        driveInLatched := driveIn
        eventResult := driveIn
        eventResult(2) := driveIn(2) && latchedReady
        latchedReady := driveIn(2)
        eventReady := True
        finishSimple(beforeByte = true)
      }
      is(0, 1, 2, 3, 4, 5, 6, 7, 10) {
        when(op === 6 && parameters(1) =/= B(0, 8 bits)) {
          executingOp := 6; preByteOwner := 6
          position := 0
          rawDeletedData := False
          readDataPending := False
          startHeaderSearch(beforeByte = true)
        }.otherwise {
          mechanicalOp := op
          beginSeek(retryTarget, dispatchOffset = sourceTickClocks)
        }
      }
      default { finish(B(0x18, 8 bits), beforeByte = true) }
    }
  }
  def completeReadSpecial(asynchronous: Boolean): Unit = {
    // The source completes READ SPECIAL on acceptance. Expose the result
    // during Dispatch; the retained synchronous path covers the old phase.
    val ramValue =
      if (asynchronous) special.readAsync(parameters(0)(4 downto 0).asUInt) else specialRead
    val ramValid =
      if (asynchronous) specialValid(parameters(0)(4 downto 0).asUInt) else specialReadValid
    val liveValue = if (asynchronous) liveSpecial else specialReadLive
    val liveValid = if (asynchronous) liveSpecialValid else specialReadLiveValid
    val trackValue =
      if (asynchronous) Mux(parameters(0)(3), currentTrack1, currentTrack0) else specialReadTrack
    val counterValue =
      if (asynchronous)
        Mux(
          parameters(0)(5 downto 0) === B(0, 6 bits),
          register0,
          Mux(parameters(0)(0), byteCounterLow, byteCounterHigh)
        )
      else specialReadCounter
    eventResult := Mux(ramValid, ramValue, B(0, 8 bits))
    when(liveValid) { eventResult := liveValue }
    when(
      parameters(0)(5 downto 0) === B(0x12, 6 bits) ||
        parameters(0)(5 downto 0) === B(0x1a, 6 bits)
    ) {
      eventResult := trackValue
    }
    when(
      parameters(0)(5 downto 0) === B(0, 6 bits) ||
        parameters(0)(5 downto 0) === B(0x13, 6 bits) ||
        parameters(0)(5 downto 0) === B(0x14, 6 bits)
    ) {
      eventResult := counterValue.asBits
    }
    when(specialMmio) {
      eventResult := 0
      switch(parameters(0)(2 downto 0)) {
        is(2) { eventResult := driveIn }
        is(3) { eventResult := driveOut }
        is(4) { eventResult := (if (asynchronous) dispatchClocks else mmioClocks) }
        is(5) { eventResult := mmioData }
      }
    }
    eventReady := (if (asynchronous) readSpecialNow else True)
    finishSimple(beforeByte = true)
  }
  when(phase === Phase.ReadSpecial) { completeReadSpecial(asynchronous = false) }
  def completeSeek(beforeIndex: Boolean = false, context: UInt = mechanicalOp): Unit = {
    mechanicalPhase := Mechanical.Idle
    // Loading the head is part of the callback, including a zero-time load.
    val loadedPins = Bits(8 bits)
    loadedPins := driveIn
    loadedPins(1) := selectedDrive && selectedPhysicalTrack === 0
    loadedPins(4) := selectedDrive && (driveOut(7) || rotation < masterClockHz / 250)
    eventDriveInCopy := loadedPins
    if (beforeIndex) { preByteDriveInCopy := loadedPins }
    driveInLatched := (driveInLatched | B(0xbb, 8 bits)) & loadedPins
    latchedReady := latchedReady && loadedPins(2)
    when(!loadedPins(2) || !latchedReady) {
      finish(B(0x10, 8 bits), beforeIndex = beforeIndex)
      when(headRate(7 downto 4) =/= 0) { driveOut(3) := True }
    }
      // A replacement command may still be waiting for parameters. The
      // outstanding mechanical callback belongs to its dispatched operation.
      .elsewhen(context === 10) { finish(B(0, 8 bits), beforeIndex = beforeIndex) }
      .otherwise {
        when(context =/= 6) {
          executingOp := context; if (beforeIndex) { preByteOwner := context }
          position := 0
          val track = parameters(0).asUInt.resize(18)
          sectorAddress := (track |<< 11) + (track |<< 9) + (parameters(1).asUInt.resize(18) |<< 8)
          readDataPending := False
          rawDeletedData := False
        }
        when(context =/= 6) {
          startHeaderSearch(beforeByte = beforeIndex)
          val high = ((U(1, 8 bits) |<< parameters(2)(7 downto 5).asUInt) - 1).resize(8)
          byteCounterLow := 128
          byteCounterHigh := high
          register0 := high
          eventRegister0 := high
          eventCounterLow := 128
          eventCounterHigh := high
        }
        when(context =/= 6) { indexCount := 3 }
        indexCallback := Mux(context === 6, U(3, 2 bits), U(1, 2 bits))
        if (beforeIndex) {
          preIndexCallback := Mux(context === 6, U(3, 2 bits), U(1, 2 bits))
          when(context =/= 6) { preIndexCount := 3 }
        }
      }
  }
  // A queued replacement took effect in the preceding source epoch. It
  // must cancel the former timer before a callback at this hardware phase.
  val replacesMechanicalCallback = dispatchPending &&
    (op <= 7 || op === 10 || op === 11 || op === 15) &&
    !(op === 6 && parameters(1) =/= 0)
  when(
    !replacesMechanicalCallback && (mechanicalPhase === Mechanical.Seek || mechanicalPhase === Mechanical.Delay) && mechanicalDelay === 0
  ) {
    // The original millisecond timer uses R8/R9 as scratch, then zeros them.
    headerPointer := 0
    idSize := 0
  }
  when(
    !replacesMechanicalCallback && mechanicalPhase === Mechanical.Seek && mechanicalDelay === 0
  ) {
    when(stepsRemaining === 0 || (calibrating && calibrationAtZero)) {
      driveOut(3) := True
      when(seekPostDelay === 0) { completeSeek(beforeIndex = true) }
        .otherwise { mechanicalDelay := seekPostDelay - 1; mechanicalPhase := Mechanical.Delay }
    }
      .otherwise {
        idSector := (stepsRemaining - 1).asBits
        // The drive output can be reprogrammed while a step timer is pending.
        stepHead(driveOut(2))
        mechanicalDelay := stepDelay - 1
      }
  }
  when(
    !replacesMechanicalCallback && mechanicalPhase === Mechanical.Delay && mechanicalDelay === 0
  ) {
    completeSeek(beforeIndex = true)
  }
  when(preIndexSpinning && index && preIndexCallback === 2) {
    indexCount := preIndexCount - 1
    when(preIndexCount === 1) {
      indexCallback := 0
      driveOut := driveOut & B(0x37, 8 bits)
      // The accepted index cell advances before the motor stops. Preserve
      // its next cell across unloading and later selection/restart.
      rotation := 64 * clocksPerUs
    }
  }
  when(preIndexCallback === 3 && preIndexSpinning && index) {
    executingOp := 6; preByteOwner := 6
    position := 0
    readDataPending := False
    indexCount := 3
    startHeaderSearch(beforeByte = true)
    indexCallback := 1
  }
  when(preIndexCallback === 1 && preIndexSpinning && index) {
    indexCount := preIndexCount - 1
    when(preIndexCount === 1) { finish(B(0x18, 8 bits), beforeByte = true) }
  }
  // Detect ID marks in the supplied FM cells instead of synthesizing headers
  // from their standard SSD positions. Each accepted cell executes at its
  // physical boundary; the preceding sync zeros distinguish a header marker.
  val anyHeader = Reg(UInt(12 bits)) init (22)
  val anyHeaderClock = (anyHeader.resize(23) * (64 * clocksPerUs)).resize(23)
  val crcClock = anyHeaderClock + 6 * 64 * clocksPerUs
  val headerMarkValid = RegInit(False)
  val headerCrc = Reg(Bits(16 bits)) init (B(65535, 16 bits))
  val headerCrcHigh = Reg(Bits(8 bits)) init (0)
  def completeSector(idCompletion: Bool): Unit = {
    indexCallback := Mux(idCompletion, U(0, 2 bits), U(1, 2 bits))
    eventDriveInCopy := driveIn
    driveInLatched := (driveInLatched | B(0xbb, 8 bits)) & driveIn
    latchedReady := latchedReady && driveIn(2)
    when(!driveIn(2) || !latchedReady) {
      finish(B(0x10, 8 bits))
    }.otherwise {
      driveOut(0) := False
      when(parameters(2)(4 downto 0) === B(1, 5 bits)) {
        parameters(2) := (parameters(2).asUInt - 1).asBits
        finish(B(0, 8 bits))
      }.otherwise {
        parameters(2) := (parameters(2).asUInt - 1).asBits
        val nextSector = sector + parameters(3)(5 downto 0).asUInt.resize(8)
        parameters(1) := nextSector.asBits
        position := 0
        when(op === 13) {
          phase := Phase.Idle
          mechanicalPhase := Mechanical.Idle
          dispatchPending := False
          indexCallback := 0
          acceptingParameters := False
          eventAcceptingParameters := False
          specifying := True
          eventSpecifying := True
          register0 := parameters(0).asUInt
          eventRegister0 := parameters(0).asUInt
          parameterCount := 3
          eventParameterCount := 3
          driveOut(0) := False
        }.otherwise {
          when(!idCompletion) { indexCount := 3 }
          startHeaderSearch()
          when(!idCompletion) {
            val nextParameter = parameters(2).asUInt - 1
            val nextHigh = ((U(1, 8 bits) |<< nextParameter(7 downto 5)) - 1).resize(8)
            register0 := nextHigh
            eventRegister0 := nextHigh
            byteCounterLow := 128
            byteCounterHigh := nextHigh
            eventCounterLow := 128
            eventCounterHigh := nextHigh
          }
        }
      }
    }
  }

  val idCrcIsReadId = command === B(0x18, 8 bits)
  val idHeaderWriteContext = mechanicalOp === 2 || mechanicalOp === 3
  def retireHeader(): Unit = {
    when(headerCrc =/= (headerCrcHigh ## io.storageData(7 downto 0))) {
      finish(B(0x0c, 8 bits))
    }.elsewhen(idCrcIsReadId) {
      completeSector(True)
    }.elsewhen(preByteIdTrack =/= parameters(0)) {
      parameterCount := nextRetryCount
      eventParameterCount := nextRetryCount
      when(nextRetryCount === 3) { finish(B(0x18, 8 bits)) }
        .otherwise {
          phase := Phase.IdCrcWait
          delay := U(64 * clocksPerUs, 24 bits) - 1
          beginSeek(nextRetryTarget, dispatchOffset = 0)
        }
    }.elsewhen(idSector === sector.asBits) {
      executingOp := mechanicalOp
      position := 0
      eventDriveInCopy := B(11, 8 bits)
      readDataPending := True
      dataSyncZeros := 3
      when(idHeaderWriteContext) {
        byteCounterLow := 5; byteCounterHigh := 0
        eventCounterLow := 5; eventCounterHigh := 0
      }
      delay := Mux(
        idHeaderWriteContext,
        U(17 * 64 * clocksPerUs, 24 bits),
        U(19 * 64 * clocksPerUs, 24 bits)
      ) - 1
      phase := Phase.Transfer
    }.otherwise {
      phase := Phase.Search
      headerSeen := False
      headerRecovering := True
      delay := U(4 * 64 * clocksPerUs, 24 bits) - 1
    }
  }
  when(preBytePhase =/= Phase.Search) { headerSeen := False; headerRecovering := False }
  when(preBytePhase === Phase.Search && preByteHeaderRecovering && delay === 0 && !indexExpires) {
    startHeaderSearch()
  }
  val scanningHeader = preBytePhase === Phase.Search && headerPresent && cellBoundary &&
    !preByteHeaderSeen && !preByteHeaderRecovering && !indexExpires
  // An invalid marker still invokes the original byte callback. Existing
  // IRQ mode can therefore deliver it, or report late DMA, before resync.
  val candidateIrqs = mode(5 downto 4) === B(3, 2 bits)
  var collectedEvent: Bool = False
  var collectedClock: Bits = B(0, 8 bits)
  var collectedData: Bits = B(0, 8 bits)
  var collectedBit: UInt = U(0, 4 bits)
  var scanAge: UInt = preSyncAge
  var scanShift: Bits = preSyncShift
  var scanCount: UInt = preSyncCount
  var scanChecking: Bool = preSyncChecking
  var scanActive: Bool = True
  for (pair <- 15 to 0 by -1) {
    val bit = if ((pair & 1) == 1) io.storageData(8 + pair / 2) else io.storageData(pair / 2)
    val followingAge = UInt(54 bits)
    val followingShift = Bits(16 bits)
    val followingCount = UInt(5 bits)
    val followingChecking = Bool()
    val followingActive = Bool()
    val followingEvent = Bool()
    val followingClock = Bits(8 bits)
    val followingData = Bits(8 bits)
    val followingBit = UInt(4 bits)
    followingAge := scanAge
    followingShift := scanShift
    followingCount := scanCount
    followingChecking := scanChecking
    followingActive := scanActive
    followingEvent := collectedEvent
    followingClock := collectedClock
    followingData := collectedData
    followingBit := collectedBit
    when(scanningHeader && scanActive) {
      when(!scanChecking) {
        when(bit === !scanAge(0)) {
          followingAge := Mux(scanAge === U(BigInt(1) << 53, 54 bits), scanAge, scanAge + 1)
        }.elsewhen(scanAge >= 32 && scanAge(0)) {
          followingChecking := True
          followingShift := B(3, 16 bits)
          followingCount := 2
          followingAge := 0
        }.otherwise {
          followingAge := Mux(bit, U(1, 54 bits), U(0, 54 bits))
        }
      }.otherwise {
        val shifted = ((scanShift |<< 1) | bit.asBits.resize(16)).resize(16)
        followingShift := shifted
        followingCount := scanCount + 1
        when(scanCount === 15) {
          val clockByte = Bits(8 bits)
          val dataByte = Bits(8 bits)
          for (j <- 0 until 8) {
            clockByte(j) := shifted(2 * j + 1)
            dataByte(j) := shifted(2 * j)
          }
          followingChecking := False
          followingAge := 0
          followingShift := 0
          followingCount := 0
          followingEvent := True
          followingClock := clockByte
          followingData := dataByte
          followingBit := 15 - pair
          when(
            (candidateIrqs && (!mode(0) || isScan || request)) ||
              (clockByte === B(0xc7, 8 bits) && dataByte === B(0xfe, 8 bits))
          ) {
            followingActive := False
          }
        }
      }
    }
    scanAge = followingAge
    scanShift = followingShift
    scanCount = followingCount
    scanChecking = followingChecking
    scanActive = followingActive
    collectedEvent = followingEvent
    collectedClock = followingClock
    collectedData = followingData
    collectedBit = followingBit
  }
  when(scanningHeader) {
    syncAge := scanAge
    syncShift := scanShift
    syncCount := scanCount
    syncChecking := scanChecking
  }
  val syncByteEvent = Bool()
  val syncCandidateClock = Bits(8 bits)
  val syncCandidateData = Bits(8 bits)
  val syncCandidateBit = UInt(4 bits)
  syncByteEvent := collectedEvent
  syncCandidateClock := collectedClock
  syncCandidateData := collectedData
  syncCandidateBit := collectedBit
  when(scanningHeader && syncByteEvent) {
    val clockByte = syncCandidateClock
    val dataByte = syncCandidateData
    val irqs = candidateIrqs
    when(irqs && (!mode(0) || isScan || request)) {
      abortNmi := nmiLevel
      serialAbort := True
      finish(B(0x0a, 8 bits))
    }.otherwise {
      when(irqs) {
        eventData := dataByte
        eventInterrupt := True
        eventNmi := True
        eventRequest := True
      }
      when(clockByte === B(0xc7, 8 bits) && dataByte === B(0xfe, 8 bits)) {
        anyHeader := nextCell
        headerMarkValid := True
        headerSeen := True
        headerCrc := addCrc(B(65535, 16 bits), B(0xfe, 8 bits))
        when(op === 6) {
          executingOp := 6
          mode := mode | B(0x30, 8 bits)
          delay := U(64 * clocksPerUs, 24 bits) - 1
          phase := Phase.Transfer
        }
      }.otherwise {
        headerPointer := 12
      }
    }
  }
  val parsingHeader = (preBytePhase === Phase.Search && preByteHeaderSeen) ||
    ((preBytePhase === Phase.Transfer || preBytePhase === Phase.SectorEnd) && isId)
  val headerWord = UInt(3 bits)
  headerWord := 0
  for (word <- 1 to 6) {
    when(rotation === anyHeaderClock + word * 64 * clocksPerUs - 1) { headerWord := word }
  }
  val searchHeaderWord = preBytePhase === Phase.Search && preByteHeaderSeen && headerWord =/= 0
  val idCrcWord = preBytePhase === Phase.SectorEnd && isId && (headerWord === 5 || headerWord === 6)
  val headerIrqWord =
    headerPresent && cellBoundary && !indexExpires && (searchHeaderWord || idCrcWord)
  val headerIrqLoss = headerIrqWord && byteDataLoss
  val idBodyLoss =
    preBytePhase === Phase.Transfer && isId && delay === 0 && !readDataPending && !indexExpires && byteDataLoss
  when(headerIrqWord) {
    when(headerIrqLoss) {
      abortNmi := nmiLevel
      serialAbort := True
      finish(B(0x0a, 8 bits))
    }.elsewhen(byteIrqs) {
      eventData := io.storageData(7 downto 0)
      eventInterrupt := True
      eventNmi := True
      eventRequest := True
    }
  }
  when(headerIrqWord && searchHeaderWord && headerWord === 4 && !headerIrqLoss) {
    mode := mode & B(0xcf, 8 bits)
  }
  when(headerPresent && parsingHeader && !indexExpires && !headerIrqLoss && !idBodyLoss) {
    for (cell <- 1 to 4) {
      when(rotation === anyHeaderClock + cell * 64 * clocksPerUs - 1) {
        headerCrc := addCrc(headerCrc, io.storageData(7 downto 0))
      }
      when(rotation === anyHeaderClock + cell * 64 * clocksPerUs) {
        headerPointer := 12 - cell
        cell match {
          case 1 => idTrack := lastCellData(7 downto 0)
          case 2 => idSide := lastCellData(7 downto 0)
          case 3 => idSector := lastCellData(7 downto 0)
          case 4 => idSize := lastCellData(7 downto 0)
        }
      }
    }
    when(rotation === anyHeaderClock + 5 * 64 * clocksPerUs - 1) {
      headerCrcHigh := io.storageData(7 downto 0)
    }
    when(rotation === crcClock - 1 && preBytePhase === Phase.Search) {
      retireHeader()
    }
  }
  when(preBytePhase === Phase.SectorEnd && isWrite) {
    when(delay === 5 * 64 * clocksPerUs - 1) { idSide := 0 }
    when(delay === 4 * 64 * clocksPerUs - 1) { idSide := 1 }
    when(delay === 3 * 64 * clocksPerUs - 1) { idSide := 2 }
  }
  when(preBytePhase === Phase.SectorEnd && delay === 0 && !indexExpires && !headerIrqLoss) {
    when(isId) { retireHeader() }
      .elsewhen(!isWrite && readCrc =/= (readCrcHigh ## io.storageData(7 downto 0))) {
        finish(B(0x0e, 8 bits))
      }
      .otherwise {
        when(isWrite) { idSide := 3 }
        completeSector(False)
      }
  }
  when(
    preBytePhase === Phase.Transfer && delay === 0 && !isWrite && !isId && !validDataClocks && !indexExpires
  ) {
    delay := U(64 * clocksPerUs, 24 bits) - 1
  }.elsewhen(preBytePhase === Phase.Transfer && delay === 0 && !readDataPending && !indexExpires) {
    when(byteDataLoss) {
      abortNmi := nmiLevel
      serialAbort := True
      finish(B(0x0a, 8 bits))
    }.otherwise {
      when(byteIrqs) {
        when(!isWrite) { eventData := io.storageData(7 downto 0) }
        eventInterrupt := True
        eventNmi := True
        eventRequest := True
      }
      delay := U(64 * clocksPerUs, 24 bits) - 1
    }
  }
  when(preBytePhase === Phase.IdCrcWait && delay === 0 && !indexExpires) {
    delay := U(64 * clocksPerUs, 24 bits) - 1
    when(byteDataLoss) {
      abortNmi := nmiLevel
      serialAbort := True
      finish(B(0x0a, 8 bits))
    }.elsewhen(byteIrqs) {
      eventData := io.storageData(7 downto 0)
      eventInterrupt := True
      eventNmi := True
      eventRequest := True
    }
  }
  when(gapByte) {
    when(gapByteLoss) {
      abortNmi := nmiLevel
      serialAbort := True
      finish(B(0x0a, 8 bits))
    }.elsewhen(byteIrqs) {
      when(!isWrite || dataSyncZeros === 3) { eventData := io.storageData(7 downto 0) }
      eventInterrupt := True
      eventNmi := True
      eventRequest := True
    }
  }
  // IRQ callbacks become visible in internal status before the first byte:
  // at the WRITE sync boundary or an accepted READ data marker. They stop
  // when the serial processor consumes the final byte, before host service.
  when(arrivingByte && isId && position(1 downto 0) === 3) {
    mode := mode & B(0xcf, 8 bits)
  }
  // A DMA-selected WRITE aborts inside the last sync-cell callback, before
  // advancing the head to the marker. Recovery detects that clock error one
  // cell earlier than an aborted marker; preserve this distinction on media.

  // The write gap decrements R21; the following write run repeats its byte.
  when(
    cellBoundary && preBytePhase === Phase.Transfer && isWrite && position === 0 && readDataPending && !indexExpires && !gapByteLoss
  ) {
    when(dataSyncZeros === 3) {
      eventDriveInCopy := (preByteDriveInCopy.asUInt - 1).asBits
      when(preByteDriveInCopy === B(1, 8 bits)) {
        dataSyncZeros := 2
        eventDriveInCopy := 0
        driveOut(0) := True
        mmioClocks := B(255, 8 bits)
        mmioData := 0
      }
    }.elsewhen(dataSyncZeros === 2) {
      val nextLow = UInt(8 bits)
      val nextHigh = UInt(8 bits)
      nextLow := byteCounterLow - 1
      nextHigh := byteCounterHigh
      when(byteCounterLow === 1) {
        nextLow := Mux(byteCounterHigh === 0, U(0, 8 bits), U(128, 8 bits))
        nextHigh := Mux(byteCounterHigh === 0, U(0, 8 bits), byteCounterHigh - 1)
      }
      byteCounterLow := nextLow
      byteCounterHigh := nextHigh
      eventCounterLow := nextLow
      eventCounterHigh := nextHigh
      when(nextLow === 0 && nextHigh === 0) {
        dataSyncZeros := 1
        readDataPending := False
        delay := U(64 * clocksPerUs, 24 bits) - 1
        mode := mode | B(0x30, 8 bits)
        mmioData := 0
      }.otherwise { mmioData := preByteDriveInCopy }
    }
  }
  when(arrivingByte && isWrite && (!byteIrqs || mode(0)) && !abortMedia) {
    mmioClocks := B(255, 8 bits)
    when(position === 0) {
      dataSyncZeros := 0
      mmioClocks := B(0xc7, 8 bits)
      mmioData := dataMarker
    }
  }
  when(arrivingByte && isWrite && (!byteIrqs || mode(0)) && !abortMedia) {
    when(position === 0) {
      writeCrc := addCrc(B(65535, 16 bits), dataMarker)
      writeCrcStage := 0
    }.otherwise { writeCrc := addCrc(writeCrc, data) }
  }
  when(
    cellBoundary && preByteGate && !preByteStopped && isWrite &&
      (preBytePhase === Phase.SectorEnd || (preBytePhase === Phase.Idle && writeCrcStage === 4))
  ) {
    switch(writeCrcStage) {
      is(0) { writeCrc := addCrc(writeCrc, data); writeCrcStage := 1 }
      is(1) { mmioData := writeCrc(15 downto 8); writeCrcStage := 2 }
      is(2) { mmioData := writeCrc(7 downto 0); writeCrcStage := 3 }
      is(3) { mmioData := B(255, 8 bits); writeCrcStage := 4 }
      is(4) { driveOut(0) := False; writeCrcStage := 5 }
    }
  }
  // Skip the ROM's eleven serial gap cells, then acquire data sync from the
  // medium. A valid marker starts the byte deadline; gap length is not assumed.
  when(
    cellBoundary && preBytePhase === Phase.Transfer && readDataPending && !isWrite && !isId && !indexExpires && !gapByteLoss
  ) {
    when(dataSyncZeros === 3) {
      eventDriveInCopy := (preByteDriveInCopy.asUInt - 1).asBits
      when(preByteDriveInCopy === B(1, 8 bits)) { dataSyncZeros := 0 }
    }
      .elsewhen(io.storageData === B(0xff00, 16 bits)) {
        when(dataSyncZeros =/= 2) { dataSyncZeros := dataSyncZeros + 1 }
      }
      .otherwise {
        dataSyncZeros := 0
        when(dataSyncZeros === 2) {
          when(!validDataMarker) {
            finish(B(0x08, 8 bits))
          }.otherwise {
            val deleted = io.storageData(7 downto 0) === B(0xf8, 8 bits)
            val irqs =
              command =/= B(0x1c, 8 bits) && (!deleted || command(3 downto 0) =/= B(0, 4 bits))
            rawDeletedData := deleted
            when(irqs) { mode := mode | B(0x30, 8 bits) }
            when(deleted) { eventResult := B(0x20, 8 bits); eventReady := True }
            readCrc := addCrc(B(65535, 16 bits), io.storageData(7 downto 0))
            readDataPending := False
            delay := U(64 * clocksPerUs, 24 bits) - 1
          }
        }
      }
  }
  when(arrivingByte && !isWrite && !isId && validDataClocks) {
    readCrc := addCrc(readCrc, io.storageData(7 downto 0))
  }
  when(preBytePhase === Phase.SectorEnd && !isWrite && !isId && delay === 64 * clocksPerUs - 1) {
    readCrcHigh := lastCellData(7 downto 0)
  }
  val serialCounterLow = Mux(isWrite && position === 0, U(128, 8 bits), byteCounterLow)
  val serialCounterHigh = Mux(isWrite && position === 0, register0, byteCounterHigh)
  // The byte processor decrements at each accepted serial byte, before the
  // host observes its request. The low counter wraps in 128-byte halves.
  when(
    arrivingByte && !isId && (!byteIrqs || (mode(
      0
    ) && !isScan)) && (isWrite || validDataClocks)
  ) {
    eventCounterLow := serialCounterLow - 1
    eventCounterHigh := serialCounterHigh
    when(serialCounterLow === 1) {
      eventCounterLow := Mux(serialCounterHigh === 0, U(0, 8 bits), U(128, 8 bits))
      eventCounterHigh := Mux(serialCounterHigh === 0, U(0, 8 bits), serialCounterHigh - 1)
    }
    byteCounterLow := eventCounterLow
    byteCounterHigh := eventCounterHigh
    when(serialCounterLow === 1 && serialCounterHigh === 0) {
      mode := mode & B(0xcf, 8 bits)
    }
  }
  // Serial progress is independent of acknowledgement, including the CRC tail.
  val byteProgress =
    arrivingByte && (!byteIrqs || (mode(0) && !isScan)) && (isWrite || isId || validDataClocks)
  val byteTransferComplete =
    Mux(isId, position(1 downto 0) === 3, eventCounterLow === 0 && eventCounterHigh === 0)
  val byteTailDelay =
    Mux(isWrite, U(5 * 64 * clocksPerUs, 24 bits), U(2 * 64 * clocksPerUs, 24 bits)) - 1
  when(byteProgress) {
    position := position + 1
    when(byteTransferComplete) {
      delay := byteTailDelay
      phase := Phase.SectorEnd
    }
  }
  when(io.read && io.address === 1) {
    hostReady := False
    hostInterrupt := False
    hostNmi := False
    nmiGap := io.nmi
  }
  // Every data-register access acknowledges NMI, including command completion
  // without a data request. Only a pending transfer advances its position.
  when((io.read || io.write) && dataAccess) {
    hostInterrupt := False
    hostNmi := False
    nmiGap := io.nmi
    hostRequest := False
  }
  // Both reset interfaces abort the controller's active work and deselect the
  // drive. Result/data and programmable/mechanical registers remain intact.
  // Result-ready also survives. Discard former parameter callbacks; a fresh
  // command accepted on this edge starts after the restart. Payload is stopped.
  when(io.warmReset || hostReset) {
    readDataPending := False
    acceptingParameters := False
    specifying := False
    eventAcceptingParameters := False
    eventSpecifying := False
    val nextCell = rotation + U(64 * clocksPerUs - 1, 23 bits)
    val rounded = (nextCell / U(64 * clocksPerUs, 23 bits)) * (64 * clocksPerUs)
    rotation := Mux(rounded >= masterClockHz / 5, U(0, 23 bits), rounded.resize(23))
    hostBusy := False
    hostInterrupt := False
    hostNmi := False
    hostRequest := False
    phase := Phase.Idle
    mechanicalPhase := Mechanical.Idle
    dispatchPending := False
    indexCallback := 0
    driveOut := 0
    readyTimeout := 0
    readyPulses := 0
    // Override serial callback mode updates from this same edge. The RAM
    // mirror write alone has lower assignment priority than those updates.
    mode := 0
    writeSpecial(U(0x17, 6 bits), B(0, 8 bits))
  }
  // Command parameters and SPECIFY share the live R0 pointer. In particular,
  // a mechanical callback may replace it with the sector-count copy first.
  val parameterPointer = eventRegister0.resize(6)
  val parameterCountAfterWrite =
    (Mux(parameterPointer === 1, io.data.asUInt, eventParameterCount) - 1).resize(8)
  val parameterCommand = Mux(parameterPointer === 31, io.data, command)
  val commandStarts = io.write && !io.warmReset && (
    (io.address === 0 && io.data(4 downto 3).orR && !io.data(1 downto 0).orR) ||
      (io.address === 1 && eventAcceptingParameters && parameterCountAfterWrite === 0)
  )
  val writeSpecialTarget = Mux(
    io.address === 1 && parameterPointer === 7,
    io.data,
    parameters(0)
  )
  val writeSpecialStarts = commandStarts &&
    Mux(io.address === 0, io.data(5 downto 2), parameterCommand(5 downto 2)) === B(14, 4 bits) &&
    physicalOperation
  val specifyStarts = commandStarts &&
    Mux(io.address === 0, io.data(5 downto 2), parameterCommand(5 downto 2)) === B(13, 4 bits) &&
    physicalOperation
  when(commandStarts) {
    // The ROM samples old drive pins and selects the new drive only after
    // its last parameter. A partial command leaves the previous motor running.
    driveInLatched := (driveInLatched | B(0xbb, 8 bits)) & driveIn
    latchedReady := latchedReady && driveIn(2)
    val newSelection = Mux(io.address === 0, io.data(7 downto 6), parameterCommand(7 downto 6))
    val previousDrive = Mux(io.warmReset, B(0, 8 bits), driveOut)
    driveOut := (previousDrive & B(0x3f, 8 bits)) | (newSelection ## B(0, 6 bits))
    when(newSelection =/= previousDrive(7 downto 6)) {
      when(spinning) { stopRotation() }
      // The 8271 clears head/step/direction on drive-selection changes,
      // preserving only side select. READY belongs to the selected drive.
      driveOut := (previousDrive & B(0x20, 8 bits)) | (newSelection ## B(0, 6 bits))
      readyTimeout := 0
      readyPulses := 0
    }
  }
  when(io.write) {
    switch(io.address) {
      is(0) {
        specifying := False
        command := io.data
        hostResult := 0
        hostReady := True
        hostBusy := True

        // Register 00 is the command parameter pointer, then the sector-count
        // high-byte copy. Its command-acceptance role must replace the old copy.
        when(!io.data(4 downto 3).orR || io.data(1 downto 0).orR) {
          register0 := 7
        }
        parameters(2) := B(1, 8 bits)
        parameters(3) := B(1, 8 bits)
        parameterCount := Mux(
          io.data(4 downto 3).orR,
          io.data(1 downto 0).asUInt.resize(3),
          U(5, 3 bits)
        ).resize(8)
        acceptingParameters := !(io.data(4 downto 3).orR && io.data(1 downto 0) === 0)
        when(
          io.data(4 downto 3).orR && io.data(
            1 downto 0
          ) === 0 && !writeSpecialStarts && !specifyStarts
        ) {
          dispatchPending := True
        }
      }
      is(1) {
        hostParameter := io.data
        hostReady := False
        when(eventAcceptingParameters && !io.warmReset) {
          writeRegister(parameterPointer, io.data, acceptedHost = true)
          when(!parameterPointer(5)) {
            mirrorSpecial(parameterPointer(4 downto 0), io.data, counters = false)
          }
          register0 := Mux(parameterPointer === 0, io.data.asUInt, eventRegister0) - 1
          parameterCount := parameterCountAfterWrite
          when(parameterCountAfterWrite === 0) {
            acceptingParameters := False
            when(!writeSpecialStarts && !specifyStarts) { dispatchPending := True }
          }
        }
        when(eventSpecifying && !eventAcceptingParameters && !io.warmReset) {
          writeRegister(parameterPointer, io.data, acceptedHost = true)
          when(!parameterPointer(5)) {
            mirrorSpecial(parameterPointer(4 downto 0), io.data, counters = false)
          }
          // SPECIFY can write its own pointer register through an alias.
          // The original increments the newly written value before the next byte.
          val replacesPointer = !parameterPointer(5) && parameterPointer(4 downto 0) === 0
          val nextPointer = Mux(replacesPointer, io.data.asUInt, eventRegister0) + 1
          register0 := nextPointer

          val nextCount = Mux(parameterPointer === 1, io.data.asUInt, eventParameterCount) - 1
          parameterCount := nextCount
          when(nextCount === 0) { finishSimple(peripheral = false) }
        }
      }
    }
  }
  when(commandStarts) { hostDriveInCopy := driveIn }
  when(specialWrite && specialAddress === 0x17) {
    hostInterrupt := specialData(3)
    hostRequest := specialData(2)
    hostBusy := specialData(7)
    when(
      dispatchPending && op === 14 ||
        eventSpecifying && !eventAcceptingParameters &&
        (Mux(parameterPointer === 1, io.data.asUInt, eventParameterCount) === 1)
    ) { hostBusy := False }
  }
  // A zero-time completion belongs to the accepted final-parameter edge,
  // before the CPU ticks. Queueing Dispatch/Delay would lose that NMI slot.
  val startCommand = Mux(io.address === 0, io.data, parameterCommand)
  val startOp = startCommand(5 downto 2).asUInt
  val startParameters = Vec(Bits(8 bits), 5)
  for (i <- 0 until 5) {
    startParameters(i) := parameters(i)
    if (i == 2 || i == 3) { when(io.address === 0) { startParameters(i) := 1 } }
    when(io.address === 1 && parameterPointer === (7 - i)) { startParameters(i) := io.data }
  }
  val startSelection = startCommand(7 downto 6)
  when(commandStarts) { command := startCommand & B(0x3c, 8 bits) }
  when(specifyStarts) {
    register0 := startParameters(0).asUInt
    parameterCount := 3
    acceptingParameters := False
    specifying := True
    mmioClocks := startSelection ## B(0, 6 bits)
  }
  when(writeSpecialStarts) {
    // Programming retains an outstanding mechanical callback. Idle commands
    // retain dispatch timing; accepted MMIO writes compensate that offset.
    val address = startParameters(0)(5 downto 0).asUInt
    val value = startParameters(1)
    mmioClocks := startSelection ## B(0, 6 bits)
    writeRegister(address, value, acceptedHost = true)
    // Mirror after parameter acceptance too, including writes to the pointer,
    // command and parameter aliases that acceptance itself may have changed.
    when(!address(5)) { mirrorSpecial(address(4 downto 0), value) }
    hostBusy := False
    acceptingParameters := False
    specifying := False
  }
  val sameSelection = startSelection === driveOut(7 downto 6)
  val startDrive = Mux(sameSelection, driveOut & B(0x3f, 8 bits), driveOut & B(0x20, 8 bits)) |
    (startSelection ## B(0, 6 bits))
  val startRequested = startParameters(0).asUInt
  val startBad0 = Mux(startCommand(7), badTrack10, badTrack00)
  val startBad1 = Mux(startCommand(7), badTrack11, badTrack01)
  val startAfterFirst = Mux(
    startRequested =/= 0 && startBad0 <= startRequested,
    (startRequested + 1).resize(8),
    startRequested
  )
  val startTarget = Mux(
    startRequested =/= 0 && startBad1 <= startAfterFirst,
    (startAfterFirst + 1).resize(8),
    startAfterFirst
  )
  val startCurrent = Mux(
    startTarget === 0,
    U(255, 8 bits),
    Mux(startCommand(7), currentTrack1, currentTrack0).asUInt
  )
  val startPhysical = Mux(startSelection(1), physicalTrack1, physicalTrack)
  val startAtZero = (startSelection(1) ^ startSelection(0)) && startPhysical === 0
  val startNoStep = startTarget === startCurrent || (startTarget === 0 && startAtZero)
  val startNoLoad = startDrive(3) || headRate(3 downto 0) === 0
  val startReady = sameSelection && startSelection === B"01" && driveIn(2) && latchedReady
  val immediateCompletion = commandStarts && startNoStep && startNoLoad &&
    (startOp <= 7 || startOp === 10) &&
    !(startOp === 6 && startParameters(1) =/= 0) && (!startReady || startOp === 10)
  when(immediateCompletion) {
    commandNmi := True
    hostNmi := True
    hostInterrupt := True
    hostReady := True
    hostBusy := False
    hostResult := Mux(io.address === 0, B(0, 8 bits), eventResult) |
      Mux(startReady, B(0, 8 bits), B(0x10, 8 bits))
    acceptingParameters := False
    specifying := False
    phase := Phase.Idle
    mechanicalPhase := Mechanical.Idle
    mechanicalDelay := 0
    dispatchPending := False
    delay := 0
    readDataPending := False
    rawDeletedData := False
    executingOp := startOp
    when(startOp === 2 || startOp === 3) {
      dataMarker := Mux(startOp === 3, B(0xf8, 8 bits), B(0xfb, 8 bits))
    }
    idSide := startTarget.asBits
    idTrack := startTarget.asBits
    when(startTarget =/= startCurrent) {
      idSector := Mux(
        startTarget >= startCurrent,
        startTarget - startCurrent,
        startCurrent - startTarget
      ).asBits
    }
    position := 0
    val track = startParameters(0).asUInt.resize(18)
    sectorAddress := (track |<< 11) + (track |<< 9) + (startParameters(1).asUInt.resize(18) |<< 8)
    mmioClocks := startSelection ## B(0, 6 bits)
    writeSpecial(Mux(startCommand(7), U(0x1a, 6 bits), U(0x12, 6 bits)), startTarget.asBits)
    when(startTarget =/= startCurrent) {
      when(startCommand(7)) { currentTrack1 := startTarget.asBits }
        .otherwise { currentTrack0 := startTarget.asBits }
      when(mode(1)) { currentTrack0 := startTarget.asBits; currentTrack1 := startTarget.asBits }
    }
    val loadedDrive = Bits(8 bits)
    loadedDrive := startDrive
    loadedDrive(3) := True
    loadedDrive(4) := startTarget >= 43
    when(startTarget =/= startCurrent) {
      loadedDrive(2) := startTarget > startCurrent
      loadedDrive(1) := False
    }
    val loadedPins = Bits(8 bits)
    loadedPins := B(0x81, 8 bits)
    loadedPins(2) := sameSelection && startSelection === B"01" && driveIn(2)
    loadedPins(1) := startAtZero
    loadedPins(4) := (startSelection(1) ^ startSelection(0)) &&
      (startSelection(1) || rotation < masterClockHz / 250)
    hostDriveInCopy := loadedPins
    driveInLatched := (((driveInLatched | B(0xbb, 8 bits)) & driveIn) | B(
      0xbb,
      8 bits
    )) & loadedPins
    latchedReady := startReady
    mode := mode & B(0xcf, 8 bits)
    indexCallback := 0
    when(headRate(7 downto 4) =/= 0 && headRate(7 downto 4) =/= 15) {
      indexCallback := 2
      indexCount := headRate(7 downto 4).asUInt.resize(8)
    }
    driveOut := Mux(startReady, loadedDrive, loadedDrive & B(0xf8, 8 bits))
    when(headRate(7 downto 4) === 0) {
      driveOut := Mux(startReady, loadedDrive & B(0x37, 8 bits), loadedDrive & B(0x30, 8 bits))
      when(spinning) { stopRotation() }
    }
  }
  // Restarting the spinning drive schedules its next cell one source tick
  // later. A read payload follows that cell, including a same-slot host ack.
  val readByteRephase = rephaseByteService && preBytePhase === Phase.Transfer &&
    !isWrite && !isId && !readDataPending && !io.warmReset && !hostReset
  when(readByteRephase) { delay := sourceTickClocks - 1 }
  // Host acceptance follows callbacks; immediate special writes can replace it.
  when(io.write && io.address === 0 && !io.warmReset) {
    when(!(writeSpecialStarts && writeSpecialTarget(5 downto 0) === B(0x17, 6 bits))) {
      mode(6) := False
    }
  }
  when(specialWrite && specialAddress === 0x1b) {
    driveInLatched := specialData
    latchedReady := specialData(2)
  }
  driveInCopy := hostDriveInCopy
  nmiLevel := hostNmi
  busy := hostBusy
  ready := hostReady
  interrupt := hostInterrupt
  request := hostRequest
  result := hostResult
  data := hostData
}
