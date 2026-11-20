ThisBuild / version := "0.1.0"
ThisBuild / scalaVersion := "2.13.12"
ThisBuild / organization := "leet.fpga"
ThisBuild / licenses := Seq("Apache-2.0" -> url("https://www.apache.org/licenses/LICENSE-2.0"))

val spinalVersion = "1.12.2"
val spinalCore = "com.github.spinalhdl" %% "spinalhdl-core" % spinalVersion
val spinalLib = "com.github.spinalhdl" %% "spinalhdl-lib" % spinalVersion
val spinalIdslPlugin = compilerPlugin(
  "com.github.spinalhdl" %% "spinalhdl-idsl-plugin" % spinalVersion
)

lazy val eliteAFpga = (project in file("."))
  .settings(
    name := "Elite-A-FPGA",
    Compile / scalaSource := baseDirectory.value / "rtl",
    Test / scalaSource := baseDirectory.value / "sim" / "tb",
    // Testbenches access Spinal's anonymous IO bundles through structural types.
    Test / scalacOptions += "-language:reflectiveCalls",
    libraryDependencies ++= Seq(
      spinalCore,
      spinalLib,
      spinalIdslPlugin,
      "org.scalatest" %% "scalatest" % "3.2.18" % Test
    ),
    // SpinalSim shells out to Verilator; keep the JVM forked.
    Test / fork := true
  )

fork := true
