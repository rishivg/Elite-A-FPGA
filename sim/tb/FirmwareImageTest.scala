package leet.sim

import java.nio.file.Files
import leet.soc.Soc
import org.scalatest.funsuite.AnyFunSuite

class FirmwareImageTest extends AnyFunSuite {
  test("missing, empty, overflowing and invalid firmware cannot silently elaborate") {
    val path = Files.createTempFile("fpgaleet-firmware", ".hex")
    try {
      intercept[IllegalArgumentException](Soc.loadHex(path.toString + "-missing", 4))
      intercept[IllegalArgumentException](Soc.loadHex(path.toString, 4))
      Files.writeString(path, "1\n2\n3\n4\n5\n")
      intercept[IllegalArgumentException](Soc.loadHex(path.toString, 4))
      for (content <- Seq("100000000\n", "-1\n")) {
        Files.writeString(path, content)
        intercept[IllegalArgumentException](Soc.loadHex(path.toString, 4))
      }
      Files.writeString(path, "12345678\nffffffff\n")
      assert(
        Soc.loadHex(path.toString, 4) == Seq(
          BigInt("12345678", 16),
          BigInt("ffffffff", 16),
          BigInt(0),
          BigInt(0)
        )
      )
    } finally Files.deleteIfExists(path)
  }
}
