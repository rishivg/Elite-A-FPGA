package leet.render

import spinal.core._
import spinal.lib._
import leet.common._

/** Model -> view transform: rotate a vertex by a row-major 3x3 fixed-point matrix, then translate.
  * One vertex per cycle in, one (registered) vertex out via `Flow`.
  *
  * Matrix and translation operands are sampled with each valid input. Flow has no backpressure; the
  * consumer must accept each valid output after the single register stage.
  */
case class Transform() extends Component {
  val io = new Bundle {
    val vIn = slave(Flow(Vec3()))
    val m = in(Vec(Fixed(), 9)) // row-major 3x3 rotation
    val t = in(Vec3()) // translation (view-space offset)
    val vOut = master(Flow(Vec3()))
  }

  // dot product of matrix row `r` with the incoming vertex
  def row(r: Int, v: Vec3): SInt =
    Fixed.mul(io.m(r * 3 + 0), v.x) +
      Fixed.mul(io.m(r * 3 + 1), v.y) +
      Fixed.mul(io.m(r * 3 + 2), v.z)

  val res = Vec3()
  res.x := (row(0, io.vIn.payload) + io.t.x).resize(Fixed.WIDTH bits)
  res.y := (row(1, io.vIn.payload) + io.t.y).resize(Fixed.WIDTH bits)
  res.z := (row(2, io.vIn.payload) + io.t.z).resize(Fixed.WIDTH bits)

  io.vOut << io.vIn.translateWith(res).stage()
}
