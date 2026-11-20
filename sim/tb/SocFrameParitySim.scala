package leet.sim

import org.scalatest.funsuite.AnyFunSuite

class SocFrameParitySim extends AnyFunSuite {
  test("native and Spinal captures match pixels, public state and frame timing", DemoFrames) {
    SocFrames.run("compare") { runner =>
      for ((input, input2) <- Seq((0L, 0L), (0xff01L, 0x10L), (0x1000000L, 0L))) {
        val frame = runner.capture(input, input2)
        assert(frame.pixels.length == 16384)
        assert(frame.pixels.forall(pixel => pixel == 0 || pixel == 1))
        assert(frame.dbg == 0xcafeL)
      }
    }
  }
}
