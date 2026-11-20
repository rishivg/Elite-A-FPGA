package leet.sim

import java.io.{BufferedInputStream, DataInputStream, File, PrintWriter}
import scala.sys.process._
import spinal.core.sim._
import leet.soc.Soc

/** Shared frame capture for the simplified demo, distinct from BBC game replay. Native mode clocks
  * the same public ports without one JVM call per RTL cycle. Set SOC_FRAME_BACKEND=spinal or
  * compare to audit it against SpinalSim.
  */
object SocFrames {
  case class Frame(pixels: Array[Int], dbg: Long, audio: Int, waited: Int)
  trait Runner {
    def capture(input: Long = 0, input2: Long = 0, limit: Int = 5000000): Frame
  }

  private var built = false
  private def build(): Unit = synchronized {
    if (!built) {
      require(
        Process(Seq("python3", "-m", "tools.validation.demo_build", "--check-only")).! == 0,
        "Run make demo-build before frame suites; nested sbt builds are unsafe"
      )
      built = true
    }
  }

  private def nativeRun(body: Runner => Unit): Unit = {
    build()
    val process = new java.lang.ProcessBuilder(new File("build/demo/native/VSoc").getAbsolutePath)
      .directory(new File("build/demo"))
      .redirectError(java.lang.ProcessBuilder.Redirect.INHERIT)
      .start()
    val commands = new PrintWriter(process.getOutputStream, true)
    val responses = new DataInputStream(new BufferedInputStream(process.getInputStream))
    val runner = new Runner {
      def capture(input: Long, input2: Long, limit: Int): Frame = {
        commands.println(s"${input & 0xffffffffL} ${input2 & 0xffffffffL} $limit")
        val dbg = responses.readInt().toLong & 0xffffffffL
        val audio = responses.readInt()
        val waited = responses.readInt()
        val bytes = new Array[Byte](16384)
        responses.readFully(bytes)
        Frame(bytes.map(_ & 0xff), dbg, audio, waited)
      }
    }
    try {
      body(runner)
      commands.close()
      require(process.waitFor() == 0, "Native demo capture failed")
    } finally {
      commands.close()
      responses.close()
      if (process.isAlive) process.destroyForcibly()
    }
  }

  private def spinalRun(body: Runner => Unit): Unit = {
    SimConfig.addSimulatorFlag("-Wno-fatal").compile(Soc()).doSim { dut =>
      dut.clockDomain.forkStimulus(period = 10)
      dut.io.fbRdAddr #= 0
      dut.io.frameAck #= false
      dut.io.input #= 0
      dut.io.input2 #= 0
      // Do not observe randomized output registers before reset and Soc's POR.
      dut.clockDomain.waitSampling(128)
      val runner = new Runner {
        def capture(input: Long, input2: Long, limit: Int): Frame = {
          dut.io.input #= (input & 0xffffffffL)
          dut.io.input2 #= (input2 & 0xffffffffL)
          var waited = 0
          while (!dut.io.frameReady.toBoolean && waited < limit) {
            dut.clockDomain.waitSampling()
            sleep(1)
            waited += 1
          }
          require(dut.io.frameReady.toBoolean, s"Frame timeout (trap=${dut.io.trap.toBoolean})")
          val dbg = dut.io.dbg.toLong
          val audio = dut.io.audioSample.toInt
          val pixels = new Array[Int](16384)
          for (address <- pixels.indices) {
            dut.io.fbRdAddr #= address
            dut.clockDomain.waitSampling()
            // Wait for the synchronous read to settle after the clock edge.
            sleep(1)
            pixels(address) = dut.io.fbRdData.toInt
          }
          dut.io.frameAck #= true
          dut.clockDomain.waitSampling()
          sleep(1)
          dut.io.frameAck #= false
          Frame(pixels, dbg, audio, waited)
        }
      }
      body(runner)
    }
  }

  def run(body: Runner => Unit): Unit = run(sys.env.getOrElse("SOC_FRAME_BACKEND", "native"))(body)

  def run(backend: String)(body: Runner => Unit): Unit = backend match {
    case "native" => nativeRun(body)
    case "spinal" => spinalRun(body)
    case "compare" =>
      nativeRun { native =>
        spinalRun { spinal =>
          var index = 0
          body(new Runner {
            def capture(input: Long, input2: Long, limit: Int): Frame = {
              val expected = spinal.capture(input, input2, limit)
              val actual = native.capture(input, input2, limit)
              val differences =
                actual.pixels.indices.filter(i => actual.pixels(i) != expected.pixels(i))
              require(differences.isEmpty, s"Frame $index pixels differ at ${differences.take(8)}")
              require(actual.dbg == expected.dbg, s"Frame $index debug differs")
              require(actual.audio == expected.audio, s"Frame $index audio differs")
              require(
                actual.waited == expected.waited,
                s"Frame $index presentation timing differs: ${actual.waited}/${expected.waited}"
              )
              index += 1
              actual
            }
          })
        }
      }
    case other => throw new IllegalArgumentException(s"Unknown SOC_FRAME_BACKEND=$other")
  }
}
