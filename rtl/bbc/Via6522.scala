package leet.bbc

import spinal.core._

/** MOS 6522 registers, timers and interrupt edges for the BBC system/user VIA. The CPU bus may
  * stretch independently of the one-MHz endpoint. PHI1 samples external serial data and PB6;
  * pulse-counted T2 runs in the low phase following accepted PB6 edges.
  */
case class Via6522() extends Component {
  val io = new Bundle {
    val tick = in Bool ()
    val phi2 = in Bool ()
    val read = in Bool ()
    val write = in Bool ()
    val address = in UInt (4 bits)
    val data = in Bits (8 bits)
    val result = out Bits (8 bits)
    val portAIn = in Bits (8 bits)
    val portBIn = in Bits (8 bits)
    val ca1 = in Bool ()
    val ca2 = in Bool ()
    val ca2AfterWrite = in Bool () // GPIO transition after this bus acknowledgement
    val cb1 = in Bool ()
    val cb1Pulse = in Bool () // both transitions occurred before this bus endpoint
    val cb2 = in Bool ()
    val portA = out Bits (8 bits)
    val driveA = out Bits (8 bits)
    val busDriveA = out Bits (8 bits)
    val portB = out Bits (8 bits)
    val busPortB = out Bits (8 bits)
    val irq = out Bool ()
    val ca2Pin = out Bool ()
    val cb1Pin = out Bool ()
    val cb1Output = out Bool ()
    val cb2Pin = out Bool ()
    val ca2Output = out Bool ()
    val cb2Output = out Bool ()
    val ca2Rise = out Bool ()
    val cb2Rise = out Bool ()
    val ca2Fall = out Bool ()
    val cb2Fall = out Bool ()
  }
  val ora = Reg(Bits(8 bits)) init (0)
  val orb = Reg(Bits(8 bits)) init (0)
  val ddra = Reg(Bits(8 bits)) init (0)
  val ddrb = Reg(Bits(8 bits)) init (0)
  val acr = Reg(Bits(8 bits)) init (0)
  // A CPU write finishing PHI2 reaches the serial latch in the following
  // PHI1. In particular, byte completion must remain visible to the CPU's
  // interrupt sample before an SR write acknowledges it. Low-phase accepted
  // writes already have this ordering and need no extra staging.
  val highSerialWrite = io.tick && io.phi2 && io.write && io.address === 10
  val serialWritePending = RegInit(False)
  val serialWriteData = Reg(Bits(8 bits)) init (0)
  val lowSerialWrite = serialWritePending && !io.phi2
  when(highSerialWrite) {
    serialWritePending := True
    serialWriteData := io.data
  }
  when(lowSerialWrite) { serialWritePending := False }
  val serial = ViaSerial()
  serial.io.tick := io.tick
  serial.io.phi2 := io.phi2
  serial.io.read := io.read
  serial.io.write := (io.write && !highSerialWrite) || lowSerialWrite
  serial.io.address := Mux(lowSerialWrite, U(10, 4 bits), io.address)
  serial.io.data := Mux(lowSerialWrite, serialWriteData, io.data)
  serial.io.mode := acr(4 downto 2)
  serial.io.cb1 := io.cb1
  serial.io.cb2 := io.cb2
  io.cb1Pin := serial.io.clockPin
  io.cb1Output := acr(4 downto 2) =/= B(0, 3 bits) && acr(3 downto 2) =/= B(3, 2 bits)
  val serialOutput = acr(4)
  val nextSerialOutput =
    Mux(io.write && io.address === 11, io.data(4), serialOutput)
  val controlPins = ViaControlPins()
  controlPins.io.tick := io.tick
  controlPins.io.write := io.write
  controlPins.io.address := io.address
  controlPins.io.data := io.data
  controlPins.io.ca1 := io.ca1
  controlPins.io.cb1 := io.cb1
  controlPins.io.cb1Pulse := io.cb1Pulse
  val pcr = controlPins.io.pcr
  io.ca2Pin := controlPins.io.ca2
  // Input-mode CB2 releases its drive bank high; the direction remains
  // controlled by PCR, and received data still comes from the actual pin.
  val serialInputReleased = !acr(4) && !controlPins.io.cb2Output
  io.cb2Pin := Mux(serialOutput, serial.io.pin, serialInputReleased || controlPins.io.cb2)
  io.ca2Output := controlPins.io.ca2Output
  io.cb2Output := serialOutput || controlPins.io.cb2Output
  io.ca2Rise := controlPins.io.ca2Rise
  // Serial pin changes precede a coincident ACR write, just as pulse expiry
  // precedes a port write. Keep both transitions when selecting another bank.
  val controlBusPin =
    Mux(controlPins.io.cb2Fall, False, controlPins.io.cb2Rise || controlPins.io.cb2)
  val beforeModeWrite = Mux(serialOutput, serial.io.busPin, controlBusPin)
  val afterModeWrite = Mux(nextSerialOutput, serial.io.busPin, controlBusPin)
  val serialRise = serialOutput && !serial.io.pin && serial.io.busPin
  val serialFall = serialOutput && serial.io.pin && !serial.io.busPin
  io.cb2Rise := (Mux(serialOutput, serialRise, controlPins.io.cb2Rise)) ||
    (!beforeModeWrite && afterModeWrite && (nextSerialOutput || controlPins.io.cb2Output))
  io.ca2Fall := controlPins.io.ca2Fall
  io.cb2Fall := (Mux(serialOutput, serialFall, controlPins.io.cb2Fall)) ||
    (beforeModeWrite && !afterModeWrite && (nextSerialOutput || controlPins.io.cb2Output))
  val enabled = Reg(Bits(7 bits)) init (0)
  val flags = Reg(Bits(7 bits)) init (0)
  serial.io.halted := flags(2)
  val timer1 = Reg(UInt(16 bits)) init (65535)
  val latch1 = Reg(UInt(16 bits)) init (65535)
  val timer2 = Reg(UInt(16 bits)) init (65535)
  val latch2 = Reg(UInt(16 bits)) init (65535)
  val armed1 = RegInit(False)
  val armed2 = RegInit(False)
  val reload1 = RegInit(False)
  val start1 = RegInit(False)
  val start2 = RegInit(False)
  val reload2 = RegInit(False)
  val pb7 = RegInit(True)
  val previousCa1 = RegNext(io.ca1) init (False)
  val previousCa2 = RegNext(io.ca2) init (False)
  val previousCb1 = RegNext(io.cb1) init (False)
  val previousCb2 = RegNext(io.cb2) init (False)
  val pinsA = (ora | ~ddra) & io.portAIn
  val pinsB = (orb | ~ddrb) & io.portBIn
  val inputA = Reg(Bits(8 bits)) init (0)
  val inputB = Reg(Bits(8 bits)) init (0)
  // PA reads physical levels; PB output bits read ORB even when an external
  // load holds the pin low (MOS 6522 peripheral-port register semantics).
  val readB = (orb & ddrb) | (pinsB & ~ddrb)
  io.portA := pinsA
  io.driveA := ora | ~ddra
  io.portB := pinsB
  when(acr(7)) { io.portB(7) := pb7 }
  // Endpoint consumers see accepted OR/DDR writes on their completing edge;
  // physical GPIO keeps its registered output timing.
  val busOra = Mux(io.write && (io.address === 1 || io.address === 15), io.data, ora)
  val busOrb = Mux(io.write && io.address === 0, io.data, orb)
  val busDdra = Mux(io.write && io.address === 3, io.data, ddra)
  val busDdrb = Mux(io.write && io.address === 2, io.data, ddrb)
  io.busDriveA := busOra | ~busDdra
  io.busPortB := (busOrb | ~busDdrb) & io.portBIn
  val setFlags = Bits(7 bits)
  setFlags := 0
  val clearFlags = Bits(7 bits)
  clearFlags := 0
  // CA1/CB1 capture on every active edge, independently of acknowledgement.
  // Enabling a latch leaves its previous capture intact until that edge.
  val activeCa1 = io.ca1 =/= previousCa1 && io.ca1 === pcr(0)
  val activeCb1 = (io.cb1 =/= previousCb1 && io.cb1 === pcr(4)) || io.cb1Pulse
  when(acr(0) && activeCa1) { inputA := pinsA }
  when(acr(1) && activeCb1) { inputB := pinsB }
  when(activeCa1) { setFlags(1) := True }
  when(activeCb1) { setFlags(4) := True }
  when(!pcr(3) && io.ca2 =/= previousCa2 && io.ca2 === pcr(2)) { setFlags(0) := True }
  when(!pcr(7) && io.cb2 =/= previousCb2 && io.cb2 === pcr(6)) { setFlags(3) := True }
  val timer1Expires = io.tick && !start1 && !reload1 && timer1 === 0
  // Clearing free-run on the underflow endpoint ends this run as well. The
  // old ACR still governs a simultaneous change from one-shot to free-run.
  val timer1OneShot = !acr(6) || (io.write && io.address === 11 && !io.data(6))
  when(io.tick) {
    when(start1) { start1 := False }
      .elsewhen(reload1) {
        timer1 := latch1
        reload1 := False
      }
      .otherwise { timer1 := timer1 - 1 }
    when(timer1Expires) {
      when(armed1) {
        setFlags(6) := True
        pb7 := !pb7
      }
      // T1 always relatches, including one-shot mode. ACR6 controls
      // repeated interrupts/PB7 transitions, not the counter's reload.
      reload1 := True
      when(timer1OneShot) { armed1 := False }
    }
  }
  // PB6 is sampled through PHI1. Its accepted falling edge counts during
  // the following low phase, independently of the endpoint-counted timer.
  val pb6Low = RegNextWhen(io.portBIn(6), !io.phi2) init (True)
  val pb6Endpoint = RegNextWhen(pb6Low, io.tick) init (True)
  val pulseRequest = RegNextWhen(pb6Endpoint && !pb6Low, io.tick) init (False)
  val previousPhi2 = RegNext(io.phi2) init (False)
  val phi1Start = previousPhi2 && !io.phi2
  // A high-phase T2 load reaches the counter in the next PHI1. Loading wins
  // a PB6 pulse at that boundary; later pulses still decrement normally.
  val pulseLoadPending = RegInit(False)
  when(phi1Start) { pulseLoadPending := False }
  when(io.write && io.address === 9) { pulseLoadPending := acr(5) && io.phi2 }
  val timer2Tick = Mux(acr(5), phi1Start && pulseRequest && !pulseLoadPending, io.tick)
  val timer2Shift = acr(4 downto 2) === B(4, 3 bits) || acr(3 downto 2) === B(1, 2 bits)
  val timer2LowUnderflow = timer2Tick && !start2 && !reload2 && timer2(7 downto 0) === 0
  // PB6 edges can arrive between VIA endpoints. Retain their underflow until
  // the serial clock-rate latch samples it; a PHI2-counted underflow is direct.
  val pulseUnderflow = RegInit(False)
  when(io.tick) { pulseUnderflow := False }
  when(acr(5) && timer2LowUnderflow) { pulseUnderflow := True }
  serial.io.timerUnderflow := timer2LowUnderflow || pulseUnderflow
  val timer2Reload = Mux(acr(5), phi1Start, io.tick)
  when(timer2Shift && reload2 && timer2Reload) {
    timer2(7 downto 0) := latch2(7 downto 0)
    reload2 := False
  }
  when(timer2Tick) {
    when(start2) { start2 := False }
      .elsewhen(timer2Shift && reload2) {
        timer2(7 downto 0) := latch2(7 downto 0)
        reload2 := False
      }
      .otherwise {
        timer2 := timer2 - 1
        // Free-running T2 shifting reloads only the low byte. The high byte
        // still borrows on underflow and retains its one-shot interrupt.
        when(timer2Shift && timer2(7 downto 0) === 0) { reload2 := True }
      }
    when(!start2 && !(timer2Shift && reload2) && timer2 === 0 && armed2) {
      setFlags(5) := True
      armed2 := False
    }
  }
  when(io.read || io.write) {
    switch(io.address) {
      is(0) {
        clearFlags(4) := True
        when(!pcr(5)) { clearFlags(3) := True }
      }
      is(1) {
        clearFlags(1) := True
        when(!pcr(1)) { clearFlags(0) := True }
      }
    }
  }
  // Serial completion follows the byte count and generated clock phase.
  // An SR access acknowledges IFR; the serial unit owns byte rearming.
  when(serial.io.completed) { setFlags(2) := True }
  when((io.read || (io.write && !highSerialWrite)) && io.address === 10 || lowSerialWrite) {
    clearFlags(2) := True
  }
  when(io.read && io.address === 4) { clearFlags(6) := True }
  when(io.read && io.address === 8) { clearFlags(5) := True }
  when(io.write) {
    switch(io.address) {
      is(0) { orb := io.data }
      is(1, 15) { ora := io.data }
      is(2) { ddrb := io.data }
      is(3) { ddra := io.data }
      is(4, 6) { latch1(7 downto 0) := io.data.asUInt }
      is(5) {
        latch1(15 downto 8) := io.data.asUInt
        timer1 := io.data.asUInt @@ latch1(7 downto 0)
        armed1 := True
        reload1 := False
        start1 := True
        pb7 := False
        clearFlags(6) := True
      }
      is(7) {
        latch1(15 downto 8) := io.data.asUInt
        clearFlags(6) := True
      }
      is(8) { latch2(7 downto 0) := io.data.asUInt }
      is(9) {
        latch2(15 downto 8) := io.data.asUInt
        timer2 := io.data.asUInt @@ latch2(7 downto 0)
        armed2 := True
        start2 := !acr(5)
        reload2 := False
        pulseUnderflow := False
        clearFlags(5) := True
      }
      is(11) {
        acr := io.data
        when(io.data(4 downto 2) =/= B(4, 3 bits) && io.data(3 downto 2) =/= B(1, 2 bits)) {
          reload2 := False
        }
      }
      is(13) { clearFlags := io.data(6 downto 0) }
      is(14) {
        when(io.data(7)) { enabled := enabled | io.data(6 downto 0) }
          .otherwise { enabled := enabled & ~io.data(6 downto 0) }
      }
    }
  }
  // Port/IFR acknowledgement follows a coincident control edge. Timer
  // underflow wins acknowledgement instead (the 6522 "just hit" window).
  val nextFlags = ((flags | setFlags) & ~clearFlags) | (setFlags & B(0x60, 7 bits))
  // An external coincident edge precedes acknowledgement. A CA2 transition
  // caused by the accepted GPIO write follows it and must remain pending.
  when(io.ca2AfterWrite && setFlags(0)) { nextFlags(0) := True }
  flags := nextFlags
  val nextEnabled = Bits(7 bits)
  nextEnabled := enabled
  when(io.write && io.address === 14) {
    nextEnabled := Mux(io.data(7), enabled | io.data(6 downto 0), enabled & ~io.data(6 downto 0))
  }
  // Synchronous bus consumers sample before the register update. Bypass
  // the value produced by this enabled edge, including read acknowledgement
  // and IER writes, so CPU polling agrees with the physical bus endpoint.
  val busFlags = Mux(io.tick, nextFlags, flags)
  val busEnabled = Mux(io.tick, nextEnabled, enabled)
  // GPIO edges also arrive on the CPU's other 2-MHz endpoint. Forward the
  // flags accepted on this edge so IRQ sampling does not wait for a VIA tick.
  // Register readback retains its separate one-MHz bus timing below.
  io.irq := (nextFlags & nextEnabled).orR
  val busPb7 = Bool()
  busPb7 := pb7
  when(timer1Expires && armed1) { busPb7 := !pb7 }
  when(io.tick && io.write && io.address === 5) { busPb7 := False }
  when(acr(7)) { io.busPortB(7) := busPb7 }
  // During the initial load delay the externally readable count is N+1;
  // the first timer edge exposes N. Keep the existing 16-bit count and
  // delay bit so loading FFFF also wraps correctly without widening it.
  val readTimer1 = timer1 + start1.asUInt.resize(16)
  val readTimer2 = UInt(16 bits)
  readTimer2 := timer2 + start2.asUInt.resize(16)
  when(timer2Shift) {
    // The initial N+1 readback wraps within the independent low counter.
    readTimer2 := timer2(15 downto 8) @@ (timer2(7 downto 0) + start2.asUInt.resize(8))
  }
  // Counter/PB7 bus readback likewise observes this edge, while external
  // pins and timer state retain their registered timing.
  val busTimer1 = UInt(16 bits)
  busTimer1 := readTimer1
  when(io.tick) { busTimer1 := Mux(start1, timer1, Mux(reload1, latch1, timer1 - 1)) }
  val busTimer2 = UInt(16 bits)
  busTimer2 := readTimer2
  when(timer2Tick) {
    busTimer2 := Mux(start2, timer2, timer2 - 1)
    when(!start2 && timer2Shift && reload2) {
      busTimer2 := timer2(15 downto 8) @@ latch2(7 downto 0)
    }
  }
  val busInputA = Mux(activeCa1, pinsA, inputA)
  val busInputB = Mux(activeCb1, pinsB, inputB)
  io.result := 0
  switch(io.address) {
    is(0) {
      io.result := (orb & ddrb) | (Mux(acr(1), busInputB, pinsB) & ~ddrb)
      when(acr(7)) { io.result(7) := busPb7 }
    }
    is(1, 15) { io.result := Mux(acr(0), busInputA, pinsA) }
    is(2) { io.result := ddrb }
    is(3) { io.result := ddra }
    is(4) { io.result := busTimer1(7 downto 0).asBits }
    is(5) { io.result := busTimer1(15 downto 8).asBits }
    is(6) { io.result := latch1(7 downto 0).asBits }
    is(7) { io.result := latch1(15 downto 8).asBits }
    is(8) { io.result := busTimer2(7 downto 0).asBits }
    is(9) { io.result := busTimer2(15 downto 8).asBits }
    is(10) { io.result := serial.io.result }
    is(11) { io.result := acr }
    is(12) { io.result := pcr }
    is(13) { io.result := io.irq ## busFlags }
    is(14) { io.result := True ## enabled }
  }
}
