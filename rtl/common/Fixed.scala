package leet.common

import spinal.core._

/** Signed Q8.8 arithmetic for the demo renderer. Intermediate products retain their full width
  * before the result is shifted and resized to 16 bits.
  */
object Fixed {
  val WIDTH = 16
  val FRAC = 8

  /** A fresh fixed-point signal. */
  def apply(): SInt = SInt(WIDTH bits)

  /** Convert a constant to a Q8.8 literal. */
  def lit(x: Double): SInt = S((x * (1 << FRAC)).round.toInt, WIDTH bits)

  /** Multiply two Q8.8 values, then truncate the fractional bits and resize.
    */
  def mul(a: SInt, b: SInt): SInt = {
    val product = a * b
    // Explicit signed slice expresses the arithmetic shift without relying
    // on Verilog's wider shift result being implicitly truncated.
    product(product.getWidth - 1 downto FRAC).resize(WIDTH bits)
  }
}

/** A fixed-point 3-component vector (model/world/view space). */
case class Vec3() extends Bundle {
  val x = Fixed()
  val y = Fixed()
  val z = Fixed()
}

/** Screen-space integer pixel coordinate.
  *
  * Width carries a few bits of headroom beyond the screen so pre-clip projected points that land
  * off-screen keep their true (large/negative) value instead of wrapping into the visible region;
  * the renderer's range check then discards them.
  */
case class Pixel(cfg: leet.common.RenderConfig) extends Bundle {
  // +5 gives ~16x the screen extent, e.g. +/-2048 for a 128-wide screen.
  // More extreme projections are explicitly flagged by Project; these bits
  // alone cannot represent every close vertex. Viewport clipping remains needed.
  val w = log2Up(cfg.screenW max cfg.screenH) + 5
  val x = SInt(w bits)
  val y = SInt(w bits)
}
