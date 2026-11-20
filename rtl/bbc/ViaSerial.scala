package leet.bbc

import spinal.core._

/** VIA serial data, byte counter and generated clocks. The one-MHz endpoint accepts CB1 edges;
  * external input data settles through the following PHI1 window. Generated CB1 returns through its
  * physical input path. Metastability and physical acceptance require chip measurements.
  */
case class ViaSerial() extends Component {
  val io = new Bundle {
    val tick = in Bool ()
    val phi2 = in Bool ()
    val read = in Bool ()
    val write = in Bool ()
    val address = in UInt (4 bits)
    val data = in Bits (8 bits)
    val mode = in Bits (3 bits)
    val timerUnderflow = in Bool ()
    val clockPin = out Bool ()
    val cb1 = in Bool ()
    val cb2 = in Bool ()
    val halted = in Bool ()
    val result = out Bits (8 bits)
    val pin = out Bool ()
    val busPin = out Bool ()
    val completed = out Bool ()
  }
  val data = Reg(Bits(8 bits)) init (0)
  val count = Reg(UInt(4 bits)) init (0)
  val running = RegInit(False)
  val previousClock = RegInit(False)
  val output = RegInit(False)
  val pending = RegInit(False)
  val completionHeld = RegInit(False)
  val inputPending = RegInit(False)
  val externalInputPending = RegInit(False)
  val inputBase = Reg(Bits(7 bits)) init (0)
  val clock = RegInit(True)
  // The clock-rate selection crosses a PHI2 latch too. Retain the old
  // selection for its completing cycle when ACR changes between endpoints.
  val clockToggle = RegNextWhen(
    (io.mode(1 downto 0) === B(2, 2 bits) ||
      ((io.mode === B(4, 3 bits) || io.mode(1 downto 0) === B(1, 2 bits)) && io.timerUnderflow)) &&
      !io.halted,
    io.tick
  ) init (False)
  val internalClock = io.mode =/= B(0, 3 bits) && io.mode(1 downto 0) =/= B(3, 2 bits)
  // The driven CB1 pin returns through the board/adapter input path.
  val serialClock = io.cb1
  val freeRunning = io.mode === B(4, 3 bits)
  val acknowledge = (io.read || io.write) && io.address === 10
  val shiftIn = !io.mode(2) && !previousClock && serialClock
  val shiftOut = io.mode(2) && previousClock && !serialClock
  val transfer = io.tick && !io.halted && !(io.completed && completionHeld) && (shiftIn || shiftOut)
  val advances = transfer && running && io.mode =/= B(0, 3 bits)
  val countBefore = RegNextWhen(count, io.tick) init (0)
  val pendingBefore = RegNextWhen(pending, io.tick) init (False)
  val outputBefore = RegNextWhen(output, io.tick) init (False)
  val advanced = RegNextWhen(advances, io.tick) init (False)
  io.busPin := output
  val nextClock = Bool()
  nextClock := clock
  when(!running || io.halted) { nextClock := True }
    .elsewhen(clockToggle) {
      nextClock := !clock
    }
  io.clockPin := clock
  io.completed := io.tick && pending && !freeRunning && (!internalClock || nextClock)
  // External input data is transparent through PHI1. Keep the seven
  // retained bits fixed while CB2 settles, then hold the sampled byte in PHI2.
  when(externalInputPending && !io.phi2) { data := inputBase ## io.cb2 }
  when(io.phi2) { externalInputPending := False }
  when(io.tick) {
    previousClock := serialClock
    clock := nextClock
    // Disabled mode still shifts data. An SR access starts a stopped byte;
    // accesses during a running byte preserve its count. IFR clear alone
    // releases shifts without starting the counter.
    // Internally clocked input crosses the following low phase before the
    // register latch accepts it. External data has its own PHI1 window.
    inputPending := transfer && shiftIn && internalClock
    when(inputPending) { data := data(6 downto 0) ## io.cb2 }
    when(transfer) {
      when(shiftOut) { data := data(6 downto 0) ## data(7) }
      when(shiftIn && !internalClock) {
        inputBase := data(6 downto 0)
        externalInputPending := True
      }
      // The output latch observes every shift, including input/disabled
      // transfers. Selecting the output bank later exposes that held bit.
      output := data(7)
      io.busPin := data(7)
      when(advances) {
        // The byte counter continues in free-running mode. Its ten phases
        // become visible if ACR selects an interrupting mode mid-transfer.
        count := Mux(count === 9, U(0, 4 bits), count + 1)
        pending := count === 7
      }
    }
  }
  when(!running) { count := 0; pending := False }
  // An access to a completed byte acknowledges IFR while retaining the
  // completion state. Its reassertion blocks a new coincident shift.
  when(!running || !pending) { completionHeld := False }
  when(acknowledge && pending) { completionHeld := True }
  when(io.mode === B(0, 3 bits) || io.halted) { running := False }
  when(acknowledge) {
    running := True
  }
  when(io.write && io.address === 10) {
    data := io.data
    inputPending := False
    externalInputPending := False
    // A write in the following low phase replaces the shifting latch and
    // suppresses its count/output update. Reads during a running transfer
    // acknowledge IFR without resetting that in-progress byte counter.
    when(running && Mux(io.tick, advances, advanced)) {
      count := Mux(io.tick, count, countBefore)
      pending := Mux(io.tick, pending, pendingBefore)
      output := Mux(io.tick, output, outputBefore)
      io.busPin := Mux(io.tick, output, outputBefore)
    }
  }
  io.result := data
  io.pin := output
}
