package leet.render.elite

import spinal.core._

/** Byte arithmetic shared by the released-code kernels. */
object EliteArithmetic {

  /** One restoring fractional-division bit; caller ensures remainder < divisor. */
  def fractionStep(remainder: UInt, quotient: UInt, divisor: UInt): (UInt, UInt) = {
    val doubled = (remainder.resize(9) |<< 1).resize(9)
    val subtract = doubled >= divisor.resize(9)
    val nextRemainder = Mux(subtract, doubled - divisor.resize(9), doubled).resize(8)
    val nextQuotient = (quotient(6 downto 0).asBits ## subtract).asUInt
    (nextRemainder, nextQuotient)
  }
}
