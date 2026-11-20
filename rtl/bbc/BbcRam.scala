package leet.bbc

import spinal.core._

/** One physical RAM port shared by CPU and video on disjoint master phases. CPU requests occur at
  * phases 15/31; video fetches at phases 0/16. Keep the CPU response until its next enabled cycle,
  * across intervening video reads.
  */
case class BbcRam(debug: Boolean = true) extends Component {
  val io = new Bundle {
    val cpuEnable = in Bool ()
    val cpuAddress = in UInt (15 bits)
    val cpuWrite = in Bool ()
    val cpuData = in Bits (8 bits)
    val cpuReadData = out Bits (8 bits)
    val videoEnable = in Bool ()
    val videoAddress = in UInt (15 bits)
    val videoReadData = out Bits (8 bits)
    val debugAddress = in UInt (15 bits)
    val debugData = out Bits (8 bits)
  }
  val memory = Mem(Bits(8 bits), 32768)
  memory.initBigInt((0 until 32768).map(i => BigInt(if (i < 256) 0 else 255)))
  val data = memory.readWriteSync(
    Mux(io.cpuEnable, io.cpuAddress, io.videoAddress),
    io.cpuData,
    enable = io.cpuEnable || io.videoEnable,
    write = io.cpuEnable && io.cpuWrite
  )
  val response = Reg(Bits(8 bits)) init (0)
  when(RegNext(io.cpuEnable) init (False)) { response := data }
  val videoReady = RegNext(io.videoEnable) init (False)
  val videoResponse = Reg(Bits(8 bits)) init (0)
  when(videoReady) { videoResponse := data }
  io.cpuReadData := response
  // The ULA consumes the byte throughout a character, across CPU accesses.
  // Bypass on the first ready cycle preserves the first pixel's RAM latency.
  io.videoReadData := Mux(videoReady, data, videoResponse)
  if (debug) {
    io.debugData := memory.readAsync(io.debugAddress)
    assert(!(io.cpuEnable && io.videoEnable), "BBC CPU/video RAM phases collided")
  } else { io.debugData := 0 }
}
