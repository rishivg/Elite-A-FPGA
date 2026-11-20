package leet.bbc

import spinal.core._

/** MOS 6522 registers, timers and interrupt edges for the BBC system/user VIA. All timing uses a 1
  * MHz enable; the CPU bus may be stretched independently. Shift-register modes are retained as
  * registers but not implemented yet.
  */
case class Via6522() extends Component {
  val io = new Bundle {
    val tick = in Bool ()
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
  }
  val ora = Reg(Bits(8 bits)) init (0)
  val orb = Reg(Bits(8 bits)) init (0)
  val ddra = Reg(Bits(8 bits)) init (0)
  val ddrb = Reg(Bits(8 bits)) init (0)
  val acr = Reg(Bits(8 bits)) init (0)
  val pcr = Reg(Bits(8 bits)) init (0)
  val shift = Reg(Bits(8 bits)) init (0)
  val enabled = Reg(Bits(7 bits)) init (0)
  val flags = Reg(Bits(7 bits)) init (0)
  val timer1 = Reg(UInt(16 bits)) init (65535)
  val latch1 = Reg(UInt(16 bits)) init (65535)
  val timer2 = Reg(UInt(16 bits)) init (65535)
  val latch2 = Reg(UInt(16 bits)) init (65535)
  val armed1 = RegInit(False)
  val armed2 = RegInit(False)
  val reload1 = RegInit(False)
  val start1 = RegInit(False)
  val start2 = RegInit(False)
  val pb7 = RegInit(True)
  val previousCa1 = RegNext(io.ca1) init (False)
  val previousCa2 = RegNext(io.ca2) init (False)
  val previousCb1 = RegNext(io.cb1) init (False)
  val previousCb2 = RegNext(io.cb2) init (False)
  val previousPb6 = RegNext(io.portBIn(6)) init (True)
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
  when(io.tick) {
    when(start1) { start1 := False }
      .elsewhen(reload1) {
        timer1 := latch1
        reload1 := False
      }
      .otherwise { timer1 := timer1 - 1 }
    when(!start1 && !reload1 && timer1 === 0) {
      when(armed1) {
        setFlags(6) := True
        pb7 := !pb7
      }
      // T1 always relatches, including one-shot mode. ACR6 controls
      // repeated interrupts/PB7 transitions, not the counter's reload.
      reload1 := True
      when(!acr(6)) { armed1 := False }
    }
  }
  val timer2Tick = Mux(acr(5), previousPb6 && !io.portBIn(6), io.tick)
  when(timer2Tick) {
    when(start2) { start2 := False }.otherwise { timer2 := timer2 - 1 }
    when(!start2 && timer2 === 0 && armed2) {
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
        clearFlags(5) := True
      }
      is(10) { shift := io.data }
      is(11) { acr := io.data }
      is(12) { pcr := io.data }
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
  io.irq := (busFlags & busEnabled).orR
  val busPb7 = Bool()
  busPb7 := pb7
  when(io.tick && !start1 && !reload1 && timer1 === 0 && armed1) { busPb7 := !pb7 }
  when(io.tick && io.write && io.address === 5) { busPb7 := False }
  when(acr(7)) { io.busPortB(7) := busPb7 }
  // During the initial load delay the externally readable count is N+1;
  // the first timer edge exposes N. Keep the existing 16-bit count and
  // delay bit so loading FFFF also wraps correctly without widening it.
  val readTimer1 = timer1 + start1.asUInt.resize(16)
  val readTimer2 = timer2 + start2.asUInt.resize(16)
  // Counter/PB7 bus readback likewise observes this edge, while external
  // pins and timer state retain their registered timing.
  val busTimer1 = UInt(16 bits)
  busTimer1 := readTimer1
  when(io.tick) { busTimer1 := Mux(start1, timer1, Mux(reload1, latch1, timer1 - 1)) }
  val busTimer2 = UInt(16 bits)
  busTimer2 := readTimer2
  when(timer2Tick) { busTimer2 := Mux(start2, timer2, timer2 - 1) }
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
    is(10) { io.result := shift }
    is(11) { io.result := acr }
    is(12) { io.result := pcr }
    is(13) { io.result := io.irq ## busFlags }
    is(14) { io.result := True ## enabled }
  }
}
