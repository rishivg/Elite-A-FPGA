package leet.render.elite

import spinal.core._

/** Public released-kernel register map, in bytes relative to the SoC MMIO base. */
object EliteRenderMap {
  val Base = 0x64
  val FaceWords = 5
  val PoseBase = 0x78
  val PoseWords = 16
  val Words = FaceWords + PoseWords
  val Last = Base + 4 * (Words - 1)

  object Command {
    val Start = 0
    val TransferFace = 1
    val Normalize = 2
    val Transpose = 3
    val Vertex = 4
  }
}

/** Own decoding and atomic inter-stage wiring for both SoC and simulations. Parent accepts
  * full-word writes only. Addresses are relative word offsets.
  */
case class EliteRenderRegisters() extends Component {
  val io = new Bundle {
    val write = in Bool ()
    val address = in UInt (log2Up(EliteRenderMap.Words) bits)
    val data = in Bits (32 bits)
    val read = out Bits (32 bits)
  }
  val faces = EliteFaceRegisters()
  val pose = ElitePoseRegisters()
  val faceSelected = io.address < EliteRenderMap.FaceWords
  val poseSelected = io.address >= EliteRenderMap.FaceWords && io.address < EliteRenderMap.Words
  faces.io.write := io.write && faceSelected
  faces.io.address := io.address.resize(3)
  faces.io.data := io.data
  pose.io.write := io.write && poseSelected
  pose.io.address := (io.address - EliteRenderMap.FaceWords).resize(4)
  pose.io.data := io.data
  pose.io.faceBusy := faces.io.busy
  faces.io.reserved := pose.io.faceReserved
  faces.io.loadDots := pose.io.faceLoad
  faces.io.loadedDots := pose.io.faceDots
  faces.io.loadedSigns := pose.io.faceSigns
  io.read := 0
  when(faceSelected) { io.read := faces.io.read }
  when(poseSelected) { io.read := pose.io.read }
}
