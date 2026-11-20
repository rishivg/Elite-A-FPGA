package leet.sim

import spinal.core._
import spinal.core.sim._
import leet.common._
import leet.render.Renderer
import org.scalatest.funsuite.AnyFunSuite
import java.io.{File, PrintWriter}

/** Drives the full hardware renderer to draw the Cobra Mk III at several rotation angles, reads the
  * framebuffer back out, and dumps build/sim/ship_NN.pbm per frame. tools/pbm2png.py turns them
  * into viewable PNGs.
  *
  * Run with `sbt "testOnly leet.sim.ShipRenderSim"`.
  */
class ShipRenderSim extends AnyFunSuite {
  val FRAC = 8
  val cfg = RenderConfig(screenW = 128, screenH = 96, colorBits = 1)
  val focal = 160
  val frames = 8
  val tilt = math.toRadians(20.0)
  val frontPositive = true // must match the Renderer parameter below

  def fx(d: Double): Long = math.round(d * (1 << FRAC))

  // tiny double-vector helpers for the ground-truth cull check
  type V3 = Array[Double]
  def m3(m: Array[Double], v: V3): V3 = Array(
    m(0) * v(0) + m(1) * v(1) + m(2) * v(2),
    m(3) * v(0) + m(4) * v(1) + m(5) * v(2),
    m(6) * v(0) + m(7) * v(1) + m(8) * v(2)
  )
  def sub3(a: V3, b: V3): V3 = Array(a(0) - b(0), a(1) - b(1), a(2) - b(2))
  def cross3(a: V3, b: V3): V3 =
    Array(a(1) * b(2) - a(2) * b(1), a(2) * b(0) - a(0) * b(2), a(0) * b(1) - a(1) * b(0))
  def dot3(a: V3, b: V3): Double = a(0) * b(0) + a(1) * b(1) + a(2) * b(2)

  test("renders rotating Cobra Mk III wireframe frames") {
    SimConfig.compile(Renderer(cfg, focal)).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      val W = cfg.screenW
      val H = cfg.screenH
      val pixels = W * H

      // init inputs
      for (i <- 0 until 9) dut.io.m(i) #= 0
      dut.io.t.x #= 0
      dut.io.t.y #= 0
      dut.io.t.z #= fx(13.0)
      dut.io.clearFirst #= true
      dut.io.start #= false
      dut.io.modelIdx #= 0 // ship blueprint 0 = Cobra-style (undriven inputs float in sim)
      dut.io.fbRdAddr #= 0
      // the direct-draw line/circle ports must be held inactive so the FSM stays
      // on the ship vertex/face/edge path (undriven inputs float in sim)
      dut.io.lineStart #= false
      dut.io.lineP0.x #= 0
      dut.io.lineP0.y #= 0
      dut.io.lineP1.x #= 0
      dut.io.lineP1.y #= 0
      dut.io.lineColor #= 0
      dut.io.circleStart #= false
      dut.io.circleCenter.x #= 0
      dut.io.circleCenter.y #= 0
      dut.io.circleRadius #= 0
      dut.io.circleColor #= 0
      dut.clockDomain.waitSampling(3)

      // R = Rx(tilt) * Ry(angle), row-major
      def matrix(a: Double): Array[Double] = {
        val (ca, sa) = (math.cos(a), math.sin(a))
        val (ct, st) = (math.cos(tilt), math.sin(tilt))
        Array(
          ca,
          0.0,
          sa,
          st * sa,
          ct,
          -st * ca,
          -ct * sa,
          st,
          ct * ca
        )
      }

      def renderFrame(a: Double, model: Int = 0): Array[Int] = {
        val m = matrix(a)
        for (i <- 0 until 9) dut.io.m(i) #= fx(m(i))
        dut.io.modelIdx #= model
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false
        var guard = 0
        while (!dut.io.done.toBoolean && guard < 2000000) {
          dut.clockDomain.waitSampling()
          guard += 1
        }
        assert(guard < 2000000, "renderer did not finish")

        // read framebuffer out
        val fb = new Array[Int](pixels)
        for (addr <- 0 until pixels) {
          dut.io.fbRdAddr #= addr
          dut.clockDomain.waitSampling()
          fb(addr) = dut.io.fbRdData.toInt
        }
        fb
      }

      // --- verify the hardware screen-space cull agrees with the 3D normal test ---
      // Hardware decides front/back from the signed area of the projected triangle;
      // here we cross-check that against rotating the outward face normal and dotting
      // with the view-space point (dot < 0 == facing the camera). Skip near-edge-on
      // faces where both metrics are ~0 and the boundary is ambiguous.
      val verts3 = Renderer.shipVerts.map(v => Array(v._1, v._2, v._3))
      val faces3 = Renderer.orient(Renderer.shipVerts, Renderer.shipFaces)
      val cxF = cfg.screenW / 2.0
      val cyF = cfg.screenH / 2.0
      var disagree = 0
      for (f <- 0 until frames) {
        val m = matrix(2 * math.Pi * f / frames)
        val tv = Array(0.0, 0.0, 7.0)
        for ((ia, ib, ic) <- faces3) {
          def view(i: Int): V3 = m3(m, verts3(i)).zip(tv).map { case (p, q) => p + q }
          val va = view(ia)
          val vb = view(ib)
          val vc = view(ic)
          def scr(p: V3) = (cxF + focal * p(0) / p(2), cyF - focal * p(1) / p(2))
          val (ax, ay) = scr(va)
          val (bx, by) = scr(vb)
          val (cx2, cy2) = scr(vc)
          val area = (bx - ax) * (cy2 - ay) - (by - ay) * (cx2 - ax)
          val hwFront = if (frontPositive) area > 0 else area < 0
          val nrm = m3(m, cross3(sub3(verts3(ib), verts3(ia)), sub3(verts3(ic), verts3(ia))))
          val trueFront = dot3(nrm, va) < 0
          if (math.abs(area) > 1.0 && math.abs(dot3(nrm, va)) > 1e-3 && hwFront != trueFront)
            disagree += 1
        }
      }
      println(s"cull-sign check: $disagree disagreement(s) vs 3D normal ground truth")
      assert(disagree == 0, s"backface cull sign is wrong ($disagree faces) — flip frontPositive")

      new File("build/sim").mkdirs()
      for (f <- 0 until frames) {
        val a = 2 * math.Pi * f / frames
        val fb = renderFrame(a)
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"frame $f nearly empty ($lit px)")

        val out = new File(f"build/sim/ship_$f%02d.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"frame $f%02d: $lit%4d px -> ${out.getName}")
      }

      // --- multi-model: draw every blueprint at the same pose; each must render
      // a non-empty, distinct silhouette (proves modelIdx selects different ROMs).
      val names =
        Seq("cobra", "sidewinder", "viper", "krait", "canister", "asteroid", "constrictor")
      val sigs = scala.collection.mutable.ArrayBuffer[(String, Int, Seq[Int])]()
      for (model <- names.indices) {
        val fb = renderFrame(math.toRadians(35.0), model)
        val lit = fb.count(_ != 0)
        assert(lit > 10, s"model ${names(model)} rendered nearly empty ($lit px)")
        // column occupancy signature — cheap silhouette fingerprint
        val cols = (0 until W).map(x => (0 until H).count(y => fb(y * W + x) != 0))
        sigs += ((names(model), lit, cols))
        val out = new File(f"build/sim/model_${names(model)}.pbm")
        val pw = new PrintWriter(out)
        pw.println("P1")
        pw.println(s"$W $H")
        for (y <- 0 until H) pw.println((0 until W).map(x => fb(y * W + x)).mkString(" "))
        pw.close()
        println(f"model ${names(model)}%-11s $lit%4d px -> ${out.getName}")
      }
      // no two blueprints may produce an identical silhouette
      for (i <- sigs.indices; j <- i + 1 until sigs.size)
        assert(
          sigs(i)._3 != sigs(j)._3,
          s"models ${sigs(i)._1} and ${sigs(j)._1} render identically (modelIdx not selecting)"
        )
    }
  }
}
