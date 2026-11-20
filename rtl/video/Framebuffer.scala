package leet.video

import spinal.core._
import leet.common._

/** Raster framebuffer with one write port and a synchronous read port. Reads return one clock after
  * the address is sampled. FramePresenter copies completed frames into separate scanout storage;
  * this component does not arbitrate presentation.
  */
case class Framebuffer(cfg: RenderConfig) extends Component {
  val io = new Bundle {
    // write port (rasteriser)
    val wr = in Bool ()
    val wrAddr = in UInt (log2Up(cfg.pixels) bits)
    val wrData = in UInt (cfg.colorBits bits)
    // read port (video scanout)
    val rdAddr = in UInt (log2Up(cfg.pixels) bits)
    val rdData = out UInt (cfg.colorBits bits)
  }

  val ram = Mem(UInt(cfg.colorBits bits), cfg.pixels)
  ram.write(io.wrAddr, io.wrData, enable = io.wr)
  io.rdData := ram.readSync(io.rdAddr)
}
