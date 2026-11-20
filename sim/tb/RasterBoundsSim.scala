package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common.RenderConfig
import leet.raster.{Bresenham, BresenhamCircle}
import org.scalatest.funsuite.AnyFunSuite
import scala.collection.mutable.ArrayBuffer

class RasterBoundsSim extends AnyFunSuite {
  test("lines spanning the signed coordinate range match every mathematical pixel") {
    val cfg = RenderConfig(screenW = 128, screenH = 128, colorBits = 1)
    SimConfig.compile(Bresenham(cfg)).doSim { dut =>
      SimTimeout(2000000)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.color #= 1
      dut.io.p0.x #= 0
      dut.io.p0.y #= 0

      dut.io.p1.x #= 0
      dut.io.p1.y #= 0
      dut.clockDomain.waitSampling(3)
      val cases = Seq(
        (-2048, -2048, 2047, 1023),
        (2047, 1023, -2048, -2048),
        (-2048, 2000, 2047, -2000),
        (-2000, -2048, 2000, 2047),
        (-2048, 0, 2047, 0),
        (0, 2047, 0, -2048)
      )
      for ((x0, y0, x1, y1) <- cases) {
        val reference = ArrayBuffer[(Int, Int)]()
        var x = x0
        var y = y0
        val dx = math.abs(x1 - x0)
        val dy = -math.abs(y1 - y0)
        var error = dx + dy
        val sx = if (x0 < x1) 1 else -1
        val sy = if (y0 < y1) 1 else -1
        var done = false
        while (!done) {
          reference += ((x, y))
          done = x == x1 && y == y1
          if (!done) {
            val e2 = 2 * error
            if (e2 >= dy) {
              error += dy
              x += sx
            }

            if (e2 <= dx) {
              error += dx
              y += sy
            }
          }
        }
        dut.io.p0.x #= x0
        dut.io.p0.y #= y0
        dut.io.p1.x #= x1
        dut.io.p1.y #= y1

        dut.io.start #= true
        dut.clockDomain.waitSampling()
        sleep(1)
        dut.io.start #= false
        val actual = ArrayBuffer[(Int, Int)]()
        var guard = 0
        while (dut.io.busy.toBoolean && guard < 5000) {
          if (dut.io.pixel.valid.toBoolean)
            actual += ((dut.io.pixel.payload.x.toInt, dut.io.pixel.payload.y.toInt))
          dut.clockDomain.waitSampling()
          sleep(1)
          guard += 1
        }
        assert(guard < 5000)
        assert(
          actual.iterator.sameElements(reference.iterator),
          s"line ($x0,$y0)->($x1,$y1), first difference ${actual.zip(reference).indexWhere(p => p._1 != p._2)}, actual size ${actual.size}, reference ${reference.size}"
        )
      }
    }
  }

  test("unsigned large circle radii and off-range coordinates do not wrap") {
    val cfg = RenderConfig(screenW = 64, screenH = 48, colorBits = 1)
    SimConfig.compile(BresenhamCircle(cfg)).doSim { dut =>
      SimTimeout(2000000)
      dut.clockDomain.forkStimulus(10)
      dut.io.start #= false
      dut.io.color #= 1
      dut.io.center.x #= 0
      dut.io.center.y #= 0

      dut.io.radius #= 0
      dut.clockDomain.waitSampling(3)
      for (
        (cx, cy, radius) <- Seq((0, 0, 512), (0, 0, 1023), (-1024, 0, 1023), (1023, -1024, 800))
      ) {
        val reference = ArrayBuffer[(Int, Int)]()
        var x = radius
        var y = 0
        var error = 1 - radius
        while (y <= x) {
          reference ++= Seq(
            (cx + x, cy + y),
            (cx - x, cy + y),
            (cx + x, cy - y),
            (cx - x, cy - y),
            (cx + y, cy + x),
            (cx - y, cy + x),
            (cx + y, cy - x),
            (cx - y, cy - x)
          ).filter { case (a, b) => a >= -1024 && a <= 1023 && b >= -1024 && b <= 1023 }
          y += 1
          if (error < 0) error += 2 * y + 1
          else {
            x -= 1
            error += 2 * (y - x) + 1
          }
        }
        dut.io.center.x #= cx
        dut.io.center.y #= cy
        dut.io.radius #= radius
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        sleep(1)
        dut.io.start #= false
        val actual = ArrayBuffer[(Int, Int)]()
        var guard = 0
        while (dut.io.busy.toBoolean && guard < 10000) {
          if (dut.io.pixel.valid.toBoolean)
            actual += ((dut.io.pixel.payload.x.toInt, dut.io.pixel.payload.y.toInt))
          dut.clockDomain.waitSampling()
          sleep(1)
          guard += 1
        }
        assert(guard < 10000)
        assert(
          actual.iterator.sameElements(reference.iterator),
          s"circle ($cx,$cy) r=$radius, actual ${actual.size} pixels, reference ${reference.size}"
        )
      }
    }
  }
}
