package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common._
import leet.render.Project
import org.scalatest.funsuite.AnyFunSuite

/** Verifies perspective projection bit-exactly against the Python golden model's `project()`
  * (sim/models/fixed_ref.py). The reference values below are produced by that exact integer
  * formula; keep the two in sync.
  *
  * Run with `sbt "testOnly leet.sim.ProjectSim"`.
  */
class ProjectSim extends AnyFunSuite {
  val FRAC = 8
  val focal = 200
  val cfg = RenderConfig(screenW = 320, screenH = 256)
  val cx = cfg.screenW / 2
  val cy = cfg.screenH / 2

  def fx(d: Double): Int = math.round(d * (1 << FRAC)).toInt

  /** Golden: mirrors fixed_ref.project — abs, truncating divide, re-sign. */
  def golden(x: Double, y: Double, z: Double): (Int, Int) = {
    val xf = fx(x)
    val yf = fx(y)
    val zf = math.max(fx(z), 1)
    val qx = (math.abs(xf).toLong * focal) / zf
    val qy = (math.abs(yf).toLong * focal) / zf
    val qxs = if (xf < 0) -qx else qx
    val qys = if (yf < 0) -qy else qy
    ((cx + qxs).toInt, (cy - qys).toInt)
  }

  test("extreme signed inputs report overflow without wrapping into the viewport") {
    val small = RenderConfig(screenW = 128, screenH = 128, viewH = 96, colorBits = 1)
    SimConfig.compile(Project(small, focal)).doSim { dut =>
      SimTimeout(2000000)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.v.x #= 0
      dut.io.v.y #= 0
      dut.io.v.z #= 1
      dut.clockDomain.waitSampling(3)
      val limit = 1 << (dut.io.p.x.getWidth - 1)
      val random = new scala.util.Random(1984)
      val cases = Seq(
        (32767, 0, 1),
        (-32768, 0, 1),
        (0, -32768, 1),
        (1024, 0, 100),
        (0, 1024, 100),
        (32767, 32767, 32767),
        (-32768, -32768, 32767),
        (123, 456, 0)
      ) ++
        Seq.fill(100)(
          (random.nextInt(65536) - 32768, random.nextInt(65536) - 32768, random.nextInt(32768))
        )
      for ((x, y, z) <- cases) {
        dut.io.v.x #= x
        dut.io.v.y #= y
        dut.io.v.z #= z
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        // Operands belong to the accepted request, regardless of later inputs.
        dut.io.v.x #= 0
        dut.io.v.y #= 0
        dut.io.v.z #= 32767
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 100) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 100)
        val px = 64 + x.toLong * focal / (z max 1)
        val py = 48 - y.toLong * focal / (z max 1)
        val overflow = px < -limit || px >= limit || py < -limit || py >= limit
        assert(dut.io.overflow.toBoolean == overflow, s"overflow ($x,$y,$z) -> ($px,$py)")
        if (!overflow) assert(dut.io.p.x.toInt == px && dut.io.p.y.toInt == py)
        else assert(dut.io.p.x.toInt == 0 && dut.io.p.y.toInt == 0)
        dut.clockDomain.waitSampling(2)
      }
    }
  }

  test("projection matches the golden model bit-exactly") {
    SimConfig.compile(Project(cfg, focal)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      dut.io.start #= false
      dut.io.v.x #= 0
      dut.io.v.y #= 0
      dut.io.v.z #= fx(1.0)
      dut.clockDomain.waitSampling(3)

      def project(x: Double, y: Double, z: Double): (Int, Int) = {
        dut.io.v.x #= fx(x)
        dut.io.v.y #= fx(y)
        dut.io.v.z #= fx(z)
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        var guard = 0
        while (!dut.io.valid.toBoolean && guard < 200) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 200, s"projection stuck for ($x,$y,$z)")
        (dut.io.p.x.toInt, dut.io.p.y.toInt)
      }

      val verts = Seq(
        (0.0, 0.0, 5.0), // dead centre
        (1.0, 1.0, 5.0), // upper-right
        (-1.0, 1.0, 5.0), // upper-left
        (1.0, -1.0, 5.0), // lower-right
        (-2.0, -2.0, 8.0), // farther, lower-left
        (3.0, 0.5, 6.0),
        (0.25, -0.75, 2.0) // close, off to the side
      )
      for ((x, y, z) <- verts) {
        val got = project(x, y, z)
        val exp = golden(x, y, z)
        assert(got == exp, s"($x,$y,$z): hw $got != golden $exp")
      }
      println(s"projection: ${verts.size} vertices match golden model")
    }
  }
}
