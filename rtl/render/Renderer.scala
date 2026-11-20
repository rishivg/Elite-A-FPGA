package leet.render

import spinal.core._
import spinal.lib.fsm._
import leet.common._
import leet.raster.{Bresenham, BresenhamCircle}

/** Hardware wireframe renderer with backface culling (hidden-line removal).
  *
  * model ROM --[rotate+translate]--> Project --> coord RAM (vertex pass) per face: signed area of
  * the projected triangle --> visible bit (face pass) clear framebuffer per edge: draw iff an
  * adjacent face is visible --> Bresenham --> fb (edge pass)
  *
  * Visibility is derived in screen space: with consistently outward-wound faces, a front-facing
  * triangle projects with one sign of signed area and a back-facing one with the other.
  * `frontPositive` selects which (flip if the ship renders inside-out). An edge is drawn if either
  * adjacent face is visible, so silhouette and front edges survive while fully-occluded edges are
  * culled.
  */
case class Renderer(
    cfg: RenderConfig,
    focal: Int = 160,
    models: Seq[(Seq[(Double, Double, Double)], Seq[(Int, Int, Int)])] = Renderer.models,
    frontPositive: Boolean = true
) extends Component {
  // Orient each model's faces and derive its edges up front.
  val built = models.map { case (vs, fs) =>
    val o = Renderer.orient(vs, fs)
    (vs, o, Renderer.edgesWithFaces(o))
  }
  val nModels = models.size

  // Every model is padded to the same (maxV, maxF, maxE) so the draw FSM keeps
  // compile-time-constant bounds and `modelIdx` only offsets the ROM base. The
  // model with the most faces also has the most edges (faces=2n, edges=3n over
  // an n-ring), so it needs no padding edges; every model that DOES need padding
  // edges has spare (degenerate) face slots, and its padding edges reference its
  // own first padding face (index = its real face count) which is never visible.
  val maxV = built.map(_._1.size).max
  val maxF = built.map(_._2.size).max
  val maxE = built.map(_._3.size).max

  def padVerts(vs: Seq[(Double, Double, Double)]) =
    vs ++ Seq.fill(maxV - vs.size)((0.0, 0.0, 0.0))
  def padFaces(fs: Seq[(Int, Int, Int)]) =
    fs ++ Seq.fill(maxF - fs.size)((0, 0, 0)) // degenerate -> never visible
  def padEdges(es: Seq[(Int, Int, Int, Int)], nf: Int) =
    es ++ Seq.fill(maxE - es.size)((0, 0, nf, nf)) // ref a padding face -> never drawn

  val allVerts = built.flatMap { case (vs, _, _) => padVerts(vs) }
  val allFaces = built.flatMap { case (_, o, _) => padFaces(o) }
  val allEdges = built.flatMap { case (_, o, es) => padEdges(es, o.size) }

  val nV = maxV // per-draw iteration counts (uniform across models)
  val nF = maxF
  val nE = maxE
  val addrW = log2Up(cfg.pixels)
  val vIdxW = log2Up(nV)
  val pxT = Pixel(cfg)
  val pxW = pxT.x.getWidth
  val romVW = log2Up(nModels * maxV)
  val romFW = log2Up(nModels * maxF)
  val romEW = log2Up(nModels * maxE)

  val io = new Bundle {
    val start = in Bool ()
    val modelIdx = in UInt (log2Up(nModels) bits) // which ship blueprint to draw
    val m = in(Vec(SInt(Fixed.WIDTH bits), 9))
    val t = in(Vec3())
    val clearFirst = in Bool () // clear the framebuffer before drawing (else accumulate)
    val busy = out Bool ()
    val done = out Bool ()
    val fbRdAddr = in UInt (addrW bits)
    val fbRdData = out UInt (cfg.colorBits bits)
    // Direct-draw line: a one-shot Bresenham into the framebuffer with no model
    // transform. Firmware uses this for chrome (stardust points, dashboard bars,
    // scanner blips) — anything that doesn't go through the ship vertex pipeline.
    val lineStart = in Bool ()
    val lineP0 = in(Pixel(cfg))
    val lineP1 = in(Pixel(cfg))
    val lineColor = in UInt (cfg.colorBits bits)
    // Direct-draw circle: a one-shot Bresenham circle into the framebuffer.
    // Firmware uses this for planets, sun outline, scanner reticle, station hull.
    val circleStart = in Bool ()
    val circleCenter = in(Pixel(cfg))
    val circleRadius = in UInt (10 bits)
    val circleColor = in UInt (cfg.colorBits bits)
  }

  // --- ROMs (all models concatenated; modelIdx picks the base) ---
  val vrx = Mem(SInt(Fixed.WIDTH bits), allVerts.map(v => Fixed.lit(v._1)))
  val vry = Mem(SInt(Fixed.WIDTH bits), allVerts.map(v => Fixed.lit(v._2)))
  val vrz = Mem(SInt(Fixed.WIDTH bits), allVerts.map(v => Fixed.lit(v._3)))
  // face = 3 vertex indices (local to the model), 5 bits each
  val faceRom =
    Mem(UInt(16 bits), allFaces.map { case (a, b, c) => U((a << 10) | (b << 5) | c, 16 bits) })
  // edge endpoints and the (up to two) adjacent faces (all local to the model)
  val edgeVtx = Mem(UInt(16 bits), allEdges.map { case (a, b, _, _) => U((a << 8) | b, 16 bits) })
  val edgeFac =
    Mem(UInt(16 bits), allEdges.map { case (_, _, f0, f1) => U((f0 << 8) | f1, 16 bits) })

  // Per-model ROM base offsets (constant LUTs indexed by the latched model).
  val curModel = Reg(UInt(log2Up(nModels) bits)) init (0)
  val vBaseLut = Vec((0 until nModels).map(i => U(i * maxV, romVW bits)))
  val fBaseLut = Vec((0 until nModels).map(i => U(i * maxF, romFW bits)))
  val eBaseLut = Vec((0 until nModels).map(i => U(i * maxE, romEW bits)))
  val vBase = vBaseLut(curModel)
  val fBase = fBaseLut(curModel)
  val eBase = eBaseLut(curModel)

  // --- working storage ---
  val coordX = Mem(SInt(pxW bits), nV)
  val coordY = Mem(SInt(pxW bits), nV)
  // framebuffer powers up cleared (matches FPGA BRAM; avoids boot garbage in sim)
  val fb =
    Mem(UInt(cfg.colorBits bits), cfg.pixels) init (Seq.fill(cfg.pixels)(U(0, cfg.colorBits bits)))
  val vis = Reg(Bits(nF bits)) init (0)
  val inFront = Reg(Bits(nV bits)) init (0) // per-vertex near-plane validity (view z > zNear)
  val coordValid = Reg(Bits(nV bits)) init (0) // projection must not wrap Pixel's range
  val zNear = S(256, Fixed.WIDTH bits) // Q8.8 1.0; crossing edges still need clipping

  val proj = Project(cfg, focal)
  val bres = Bresenham(cfg)
  val circ = BresenhamCircle(cfg)
  circ.io.start := False
  circ.io.center.x := io.circleCenter.x
  circ.io.center.y := io.circleCenter.y
  circ.io.radius := io.circleRadius
  circ.io.color := io.circleColor

  // --- counters ---
  val vIdx = Reg(UInt(log2Up(nV + 1) bits)) init (0)
  val fIdx = Reg(UInt(log2Up(nF + 1) bits)) init (0)
  val eIdx = Reg(UInt(log2Up(nE + 1) bits)) init (0)
  val clrAddr = Reg(UInt(addrW bits)) init (0)
  val clearReg = Reg(Bool()) init (True) // whether this draw clears first (latched at start)

  // --- inline transform of the current vertex (vertex pass) ---
  val vAddr = vIdx.resize(vIdxW bits) // local: coordX/coordY/inFront
  val vrAddr = (vBase + vIdx).resize(romVW bits) // global: vertex ROM
  val mx = vrx.readAsync(vrAddr)
  val my = vry.readAsync(vrAddr)
  val mz = vrz.readAsync(vrAddr)
  def row(r: Int): SInt =
    Fixed.mul(io.m(r * 3), mx) + Fixed.mul(io.m(r * 3 + 1), my) + Fixed.mul(io.m(r * 3 + 2), mz)
  proj.io.start := False
  proj.io.v.x := (row(0) + io.t.x).resize(Fixed.WIDTH bits)
  proj.io.v.y := (row(1) + io.t.y).resize(Fixed.WIDTH bits)
  proj.io.v.z := (row(2) + io.t.z).resize(Fixed.WIDTH bits)
  val coordWe = False
  coordX.write(vAddr, proj.io.p.x, enable = coordWe)
  coordY.write(vAddr, proj.io.p.y, enable = coordWe)

  // --- face visibility (face pass): signed area of the projected triangle ---
  val fWord = faceRom.readAsync((fBase + fIdx).resize(romFW bits))
  val fa = fWord(14 downto 10).resize(vIdxW bits)
  val fb_ = fWord(9 downto 5).resize(vIdxW bits)
  val fc = fWord(4 downto 0).resize(vIdxW bits)
  val ax = coordX.readAsync(fa)
  val ay = coordY.readAsync(fa)
  val bx = coordX.readAsync(fb_)
  val by = coordY.readAsync(fb_)
  val cxv = coordX.readAsync(fc)
  val cyv = coordY.readAsync(fc)
  val abx = bx.resize(pxW + 1) - ax.resize(pxW + 1)
  val aby = by.resize(pxW + 1) - ay.resize(pxW + 1)
  val acx = cxv.resize(pxW + 1) - ax.resize(pxW + 1)
  val acy = cyv.resize(pxW + 1) - ay.resize(pxW + 1)
  val area = (abx * acy).resize(2 * (pxW + 1) + 1) - (aby * acx).resize(2 * (pxW + 1) + 1)
  val faceSign = if (frontPositive) area > 0 else area < 0
  val faceVisible = faceSign && coordValid(fa) && coordValid(fb_) && coordValid(fc)

  // --- edge lookup (edge pass) ---
  val eVtx = edgeVtx.readAsync((eBase + eIdx).resize(romEW bits))
  val eFac = edgeFac.readAsync((eBase + eIdx).resize(romEW bits))
  val ea = eVtx(15 downto 8).resize(vIdxW bits)
  val eb = eVtx(7 downto 0).resize(vIdxW bits)
  val ef0 = eFac(15 downto 8).resize(log2Up(nF) bits)
  val ef1 = eFac(7 downto 0).resize(log2Up(nF) bits)
  // draw an edge only if a neighbouring face is visible AND both endpoints are in
  // front of the near plane (cull edges that cross behind the camera)
  val edgeDrawn =
    (vis(ef0) || vis(ef1)) && inFront(ea) && inFront(eb) && coordValid(ea) && coordValid(eb)
  // lineMode=True selects the external line ports as Bresenham inputs (chrome
  // direct-draw path); else the edge-pass coordinates feed it (ship draw path).
  val lineMode = Reg(Bool()) init (False)
  bres.io.start := False
  bres.io.color := lineMode ? io.lineColor | U(1).resize(cfg.colorBits bits)
  bres.io.p0.x := lineMode ? io.lineP0.x | coordX.readAsync(ea)
  bres.io.p0.y := lineMode ? io.lineP0.y | coordY.readAsync(ea)
  bres.io.p1.x := lineMode ? io.lineP1.x | coordX.readAsync(eb)
  bres.io.p1.y := lineMode ? io.lineP1.y | coordY.readAsync(eb)

  // --- framebuffer write port (clear vs raster) ---
  val fbWe = False
  val fbAddr = UInt(addrW bits)
  fbAddr := clrAddr
  val fbData = UInt(cfg.colorBits bits)
  fbData := 0
  fb.write(fbAddr, fbData, enable = fbWe)
  // synchronous read so the framebuffer maps to block RAM (1-cycle scanout latency)
  io.fbRdData := fb.readSync(io.fbRdAddr)

  io.busy := True
  io.done := False

  val fsm = new StateMachine {
    val sIdle, sVtxStart, sVtxWait, sFace, sClear, sEdgeStart, sEdgeDraw, sLineStart, sLineDraw,
        sCircleStart, sCircleDraw, sDone = new State
    setEntry(sIdle)

    sIdle.whenIsActive {
      io.busy := False
      when(io.start) {
        vIdx := 0
        vis := 0
        inFront := 0
        coordValid := 0
        clearReg := io.clearFirst
        curModel := io.modelIdx
        lineMode := False
        goto(sVtxStart)
      }.elsewhen(io.lineStart) {
        lineMode := True
        goto(sLineStart)
      }.elsewhen(io.circleStart) {
        goto(sCircleStart)
      }
    }

    sVtxStart.whenIsActive {
      proj.io.start := True
      goto(sVtxWait)
    }

    sVtxWait.whenIsActive {
      when(proj.io.valid) {
        coordWe := True
        coordValid(vAddr) := !proj.io.overflow
        inFront(vAddr) := proj.io.v.z > zNear // record near-plane validity of this vertex
        vIdx := vIdx + 1
        when(vIdx === nV - 1) {
          fIdx := 0
          goto(sFace)
        }.otherwise { goto(sVtxStart) }
      }
    }

    sFace.whenIsActive {
      vis(fIdx.resize(log2Up(nF) bits)) := faceVisible
      fIdx := fIdx + 1
      when(fIdx === nF - 1) {
        when(clearReg) {
          clrAddr := 0
          goto(sClear)
        }
          .otherwise {
            eIdx := 0
            goto(sEdgeStart)
          }
      }
    }

    sClear.whenIsActive {
      fbWe := True
      fbAddr := clrAddr
      fbData := 0
      clrAddr := clrAddr + 1
      when(clrAddr === cfg.pixels - 1) {
        eIdx := 0
        goto(sEdgeStart)
      }
    }

    def advanceEdge(): Unit = {
      eIdx := eIdx + 1
      when(eIdx === nE - 1) { goto(sDone) }.otherwise { goto(sEdgeStart) }
    }

    sEdgeStart.whenIsActive {
      when(edgeDrawn) {
        bres.io.start := True
        goto(sEdgeDraw)
      }
        .otherwise { advanceEdge() }
    }

    sEdgeDraw.whenIsActive {
      val px = bres.io.pixel.payload.x
      val py = bres.io.pixel.payload.y
      // Ship edges clip to the space view (top `viewHeight` rows) so dashboard
      // chrome below it is never overdrawn by a close ship's silhouette.
      val inRange = px >= 0 && px <= (cfg.screenW - 1) && py >= 0 && py <= (cfg.viewHeight - 1)
      when(bres.io.pixel.valid && inRange) {
        fbWe := True
        fbAddr := (py.asUInt.resize(addrW bits) * cfg.screenW + px.asUInt).resize(addrW bits)
        fbData := bres.io.pcol
      }
      when(!bres.io.busy) { advanceEdge() }
    }

    sLineStart.whenIsActive {
      bres.io.start := True
      goto(sLineDraw)
    }

    sLineDraw.whenIsActive {
      val px = bres.io.pixel.payload.x
      val py = bres.io.pixel.payload.y
      val inRange = px >= 0 && px <= (cfg.screenW - 1) && py >= 0 && py <= (cfg.screenH - 1)
      when(bres.io.pixel.valid && inRange) {
        fbWe := True
        fbAddr := (py.asUInt.resize(addrW bits) * cfg.screenW + px.asUInt).resize(addrW bits)
        fbData := bres.io.pcol
      }
      when(!bres.io.busy) {
        lineMode := False
        goto(sDone)
      }
    }

    sCircleStart.whenIsActive {
      circ.io.start := True
      goto(sCircleDraw)
    }

    sCircleDraw.whenIsActive {
      val px = circ.io.pixel.payload.x
      val py = circ.io.pixel.payload.y
      val inRange = px >= 0 && px <= (cfg.screenW - 1) && py >= 0 && py <= (cfg.screenH - 1)
      when(circ.io.pixel.valid && inRange) {
        fbWe := True
        fbAddr := (py.asUInt.resize(addrW bits) * cfg.screenW + px.asUInt).resize(addrW bits)
        fbData := circ.io.pcol
      }
      when(!circ.io.busy) { goto(sDone) }
    }

    sDone.whenIsActive {
      io.done := True
      goto(sIdle)
    }
  }
}

object Renderer {
  // Flat arrowhead hull (Cobra-style top profile) with a cockpit bump and belly:
  // a convex bipyramid over a 7-vertex perimeter ring (y=0 plane) + top/bottom apexes.
  // Convex, so orient() recovers correct outward winding for the screen-space cull.
  // Model space: +z = nose forward, +y = up, +x = right.
  val shipVerts: Seq[(Double, Double, Double)] = Seq(
    (0.0, 0.0, 4.0), // 0 nose
    (1.8, 0.0, 1.5), // 1 fwd-right
    (3.5, 0.0, -1.0), // 2 wingtip-right
    (1.2, 0.0, -2.5), // 3 tail-right
    (-1.2, 0.0, -2.5), // 4 tail-left
    (-3.5, 0.0, -1.0), // 5 wingtip-left
    (-1.8, 0.0, 1.5), // 6 fwd-left
    (0.0, 1.0, -0.3), // 7 cockpit (top apex)
    (0.0, -0.7, -0.3) // 8 belly (bottom apex)
  )
  // perimeter ring (clockwise in top view); each ring edge spans to top and bottom apex
  private val ring = Vector(0, 1, 2, 3, 4, 5, 6)
  val shipFaces: Seq[(Int, Int, Int)] = ring.indices.flatMap { i =>
    val a = ring(i)
    val b = ring((i + 1) % ring.size)
    Seq((7, a, b), (8, b, a)) // top apex + bottom apex (orient() fixes winding)
  }

  private type V = (Double, Double, Double)
  private def sub(a: V, b: V): V = (a._1 - b._1, a._2 - b._2, a._3 - b._3)
  private def cross(a: V, b: V): V =
    (a._2 * b._3 - a._3 * b._2, a._3 * b._1 - a._1 * b._3, a._1 * b._2 - a._2 * b._1)
  private def dot(a: V, b: V): Double = a._1 * b._1 + a._2 * b._2 + a._3 * b._3

  /** Reorder each face's vertices so its normal points away from the origin (outward), giving every
    * face a consistent winding for the screen-space cull.
    */
  def orient(verts: Seq[V], faces: Seq[(Int, Int, Int)]): Seq[(Int, Int, Int)] =
    faces.map { case (a, b, c) =>
      val (va, vb, vc) = (verts(a), verts(b), verts(c))
      val n = cross(sub(vb, va), sub(vc, va))
      val centroid =
        ((va._1 + vb._1 + vc._1) / 3, (va._2 + vb._2 + vc._2) / 3, (va._3 + vb._3 + vc._3) / 3)
      if (dot(n, centroid) < 0) (a, c, b) else (a, b, c)
    }

  /** Unique edges with their (up to two) adjacent face indices. */
  def edgesWithFaces(faces: Seq[(Int, Int, Int)]): Seq[(Int, Int, Int, Int)] = {
    val m = scala.collection.mutable.LinkedHashMap[(Int, Int), List[Int]]()
    faces.zipWithIndex.foreach { case ((a, b, c), fi) =>
      Seq((a, b), (b, c), (c, a)).foreach { case (u, v) =>
        val k = if (u < v) (u, v) else (v, u)
        m(k) = m.getOrElse(k, Nil) :+ fi
      }
    }
    m.toSeq.map { case ((u, v), fs) => (u, v, fs.head, if (fs.size > 1) fs(1) else fs.head) }
  }

  /** A convex bipyramid: an n-vertex perimeter ring at y=0 plus a top and a bottom apex; every ring
    * edge fans to both apexes. orient() recovers the outward winding for the screen-space cull (the
    * hull must stay convex).
    */
  def bipyramid(
      ring: Seq[(Double, Double, Double)],
      topY: Double,
      botY: Double,
      apexZ: Double
  ): (Seq[(Double, Double, Double)], Seq[(Int, Int, Int)]) = {
    val n = ring.size
    val verts = ring ++ Seq((0.0, topY, apexZ), (0.0, botY, apexZ))
    val (top, bot) = (n, n + 1)
    val faces = (0 until n).flatMap { i =>
      val a = i
      val b = (i + 1) % n
      Seq((top, a, b), (bot, b, a))
    }
    (verts, faces)
  }

  // Additional demo hulls use distinct convex perimeter rings and two apexes.
  val sidewinder = bipyramid(
    Seq( // narrow flat diamond dart
      (0.0, 0.0, 3.0),
      (2.2, 0.0, -0.5),
      (0.0, 0.0, -2.6),
      (-2.2, 0.0, -0.5)
    ),
    0.6,
    -0.6,
    -0.4
  )
  val viper = bipyramid(
    Seq( // long swept interceptor with a wide tail
      (0.0, 0.0, 4.2),
      (1.5, 0.0, -1.0),
      (2.1, 0.0, -3.0),
      (-2.1, 0.0, -3.0),
      (-1.5, 0.0, -1.0)
    ),
    0.9,
    -0.5,
    -1.0
  )
  val krait = bipyramid(
    Seq( // broad hexagonal star fighter
      (0.0, 0.0, 3.2),
      (2.6, 0.0, 0.6),
      (2.4, 0.0, -2.0),
      (0.0, 0.0, -3.2),
      (-2.4, 0.0, -2.0),
      (-2.6, 0.0, 0.6)
    ),
    1.3,
    -1.3,
    -0.6
  )
  // Cargo canister: a small chunky triangular bipyramid that tumbles.
  val canister = bipyramid(
    Seq(
      (1.2, 0.0, 0.0),
      (-0.6, 0.0, 1.0),
      (-0.6, 0.0, -1.0)
    ),
    1.0,
    -1.0,
    0.0
  )
  // Asteroid: a lumpy (irregular but convex) hexagonal rock.
  val asteroid = bipyramid(
    Seq(
      (2.2, 0.0, 0.4),
      (1.4, 0.0, 1.8),
      (-0.8, 0.0, 2.0),
      (-2.0, 0.0, 0.2),
      (-1.2, 0.0, -1.8),
      (1.0, 0.0, -2.0)
    ),
    1.6,
    -1.4,
    0.2
  )
  // Constrictor: the mission-1 prototype — a long, sharp, predatory dart with
  // a tall dorsal spine (deep apexes), so it reads as meaner than the fighters.
  val constrictor = bipyramid(
    Seq(
      (0.0, 0.0, 4.5),
      (2.4, 0.0, 0.0),
      (1.0, 0.0, -3.2),
      (-1.0, 0.0, -3.2),
      (-2.4, 0.0, 0.0)
    ),
    1.8,
    -1.0,
    -0.5
  )

  /** Blueprints, indexed by modelIdx (must match the firmware MODEL_* enum). 0 = Cobra-style (the
    * original).
    */
  val models: Seq[(Seq[(Double, Double, Double)], Seq[(Int, Int, Int)])] =
    Seq((shipVerts, shipFaces), sidewinder, viper, krait, canister, asteroid, constrictor)
}
