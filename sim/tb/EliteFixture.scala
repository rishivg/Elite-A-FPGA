package leet.sim

import scala.io.Source

/** Released-code byte fixtures; reject absent, empty or malformed inputs. */
object EliteFixture {
  def load(name: String, width: Int, minimum: Int): Vector[Array[Int]] = {
    val source = Source.fromFile(
      sys.env.getOrElse(
        name,
        throw new IllegalArgumentException(s"Run make validation-render for $name")
      )
    )
    val rows =
      try source.getLines().map(_.split("\\s+").map(_.toInt)).toVector
      finally source.close()
    require(
      rows.size >= minimum && rows.forall(r =>
        r.length == width && r.forall(v => v >= 0 && v <= 255)
      ),
      s"Malformed or incomplete $name fixture"
    )
    rows
  }
  def packed(values: Seq[Int]): BigInt =
    values.zipWithIndex.map { case (value, index) => BigInt(value) << (8 * index) }.sum
  def word(values: Seq[Int]): Long = {
    require(values.size <= 4)
    packed(values).toLong
  }
}
