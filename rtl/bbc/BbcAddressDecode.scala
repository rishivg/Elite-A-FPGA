package leet.bbc

import spinal.core._

/** Model B device aliases and slow-bus groups. Reads from unimplemented SHEILA registers expose the
  * address high byte; absent Econet registers and FRED/JIM return FF. No Tube is fitted.
  */
case class BbcAddressDecode() extends Component {
  val io = new Bundle {
    val address = in UInt (16 bits)
    val crtc = out Bool ()
    val acia = out Bool ()
    val serial = out Bool ()
    val systemVia = out Bool ()
    val userVia = out Bool ()
    val fdc = out Bool ()
    val adc = out Bool ()
    val ula = out Bool ()
    val romSelect = out Bool ()
    val slow = out Bool ()
    val unimplemented = out Bits (8 bits)
  }
  val address = io.address
  // Compare only decoded address bits. Low bits select registers or mirrors;
  // the base remains a readable BBC bus address instead of a shifted constant.
  def region(base: Int, ignoredBits: Int): Bool =
    address(ignoredBits, 16 - ignoredBits bits) === (base >> ignoredBits)

  io.crtc := region(0xfe00, 3) // FE00..FE07
  io.acia := region(0xfe08, 3) // FE08..FE0F
  io.serial := region(0xfe10, 3) // FE10..FE17
  io.systemVia := region(0xfe40, 5) // FE40..FE5F
  io.userVia := region(0xfe60, 5) // FE60..FE7F
  io.fdc := region(0xfe80, 5) // FE80..FE9F
  io.adc := region(0xfec0, 5) // FEC0..FEDF
  io.ula := region(0xfe20, 4) // FE20..FE2F
  // Model B's FE38..FE3B hole is distinct from the FE34 and FE3C aliases.
  io.romSelect := region(0xfe30, 3) || region(0xfe3c, 2)
  // SHEILA's 32-byte groups: CRTC/ACIA/serial, ULA/ROM, system VIA,
  // user VIA, FDC, Econet, ADC and Tube. FRED/JIM are always slow.
  val slowSheila = Vec(True, False, True, True, False, False, True, False)(address(5, 3 bits))
  io.slow := address >= 0xfc00 && address < 0xff00 && (address < 0xfe00 || slowSheila)
  io.unimplemented := address(8, 8 bits).asBits
  when(address < 0xfe00 || region(0xfe18, 2) || region(0xfe20, 2)) {
    io.unimplemented := B(255, 8 bits)
  }
}

object GenerateBbcBusDevices extends App {
  val config = SpinalConfig(
    targetDirectory = args.headOption.getOrElse("build/bbc/bus-devices"),
    defaultConfigForClockDomains = ClockDomainConfig(resetActiveLevel = HIGH)
  )
  config.generateVerilog(BbcAddressDecode())
  config.generateVerilog(BbcSerial())
}
