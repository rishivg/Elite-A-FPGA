package leet.render.elite

import spinal.core._
import EliteRenderMap.Command

/** CPU operands and sequencing for original orientation, LL51 and vertices. Busy writes/starts are
  * ignored; this bank holds kernel operands, avoiding duplicate snapshots. Normalization replaces
  * the matrix bank atomically. Face-transfer commands reserve faces throughout the complete
  * operation.
  */
case class ElitePoseRegisters() extends Component {
  val io = new Bundle {
    val write = in Bool ()
    val address = in UInt (4 bits)
    val data = in Bits (32 bits)
    val read = out Bits (32 bits)
    val faceBusy = in Bool ()
    val faceReserved = out Bool ()
    val faceLoad = out Bool ()
    val faceDots = out Bits (24 bits)
    val faceSigns = out Bits (3 bits)
  }
  object Phase extends SpinalEnum {
    val Idle, Normalize, LaunchTransform, Transform, LaunchVertex, Vertex = newElement()
  }
  val phase = Reg(Phase()) init (Phase.Idle)
  val busy = phase =/= Phase.Idle
  val vector = Reg(Bits(48 bits)) init (0)
  val matrix = Reg(Bits(144 bits)) init (0)
  val world = Reg(Bits(72 bits)) init (0)
  val done = RegInit(False)
  val transferFace = RegInit(False)
  val renderVertex = RegInit(False)
  val orientation = EliteOrientation(captureOperands = false)
  val transform = EliteMatrixVector(captureOperands = false)
  val vertex = EliteVertex(captureOperands = false)
  val start = io.write && io.address === 7 && io.data(Command.Start) && !busy &&
    (!io.data(Command.TransferFace) || !io.faceBusy)

  orientation.io.start := start && io.data(Command.Normalize)
  orientation.io.raw := matrix
  orientation.io.transpose := io.data(Command.Transpose)
  transform.io.start := (start && !io.data(Command.Normalize)) || phase === Phase.LaunchTransform
  transform.io.vector := vector
  transform.io.matrix := matrix
  vertex.io.start := phase === Phase.LaunchVertex
  vertex.io.world := world
  vertex.io.rotated := transform.io.result
  val transformed = phase === Phase.Transform && transform.io.valid
  val completed = (transformed && !renderVertex) || (phase === Phase.Vertex && vertex.io.valid)

  when(start) {
    done := False
    transferFace := io.data(Command.TransferFace)
    renderVertex := io.data(Command.Vertex)
    phase := Mux(io.data(Command.Normalize), Phase.Normalize, Phase.Transform)
  }
  when(phase === Phase.Normalize && orientation.io.valid) {
    matrix := orientation.io.result
    phase := Phase.LaunchTransform
  }
  when(phase === Phase.LaunchTransform) { phase := Phase.Transform }
  when(transformed && renderVertex) { phase := Phase.LaunchVertex }
  when(phase === Phase.LaunchVertex) { phase := Phase.Vertex }
  when(completed) {
    done := True
    phase := Phase.Idle
  }

  io.faceReserved := transferFace && busy
  io.faceLoad := transferFace && transformed
  for (i <- 0 until 3) {
    io.faceDots(8 * i, 8 bits) := transform.io.result(16 * i, 8 bits)
    io.faceSigns(i) := transform.io.result(16 * i + 15)
  }
  when(io.write && !busy) {
    switch(io.address) {
      is(0) { vector(31 downto 0) := io.data }
      is(1) { vector(47 downto 32) := io.data(15 downto 0) }
      for (i <- 0 until 4) is(2 + i) { matrix(32 * i, 32 bits) := io.data }
      is(6) { matrix(143 downto 128) := io.data(15 downto 0) }
      is(10) { world(31 downto 0) := io.data }
      is(11) { world(63 downto 32) := io.data }
      is(12) { world(71 downto 64) := io.data(7 downto 0) }
    }
  }
  io.read := 0
  switch(io.address) {
    is(0) { io.read := vector(31 downto 0) }
    is(1) { io.read := vector(47 downto 32).resize(32) }
    for (i <- 0 until 4) is(2 + i) { io.read := matrix(32 * i, 32 bits) }
    is(6) { io.read := matrix(143 downto 128).resize(32) }
    is(7) {
      io.read(0) := busy
      io.read(1) := done
    }
    is(8) { io.read := transform.io.result(31 downto 0) }
    is(9) { io.read := transform.io.result(47 downto 32).resize(32) }
    is(10) { io.read := world(31 downto 0) }
    is(11) { io.read := world(63 downto 32) }
    is(12) { io.read := world(71 downto 64).resize(32) }
    is(13) { io.read := vertex.io.reduced(31 downto 0) }
    is(14) { io.read := vertex.io.reduced(63 downto 32) }
    is(15) { io.read := vertex.io.result }
  }
}
