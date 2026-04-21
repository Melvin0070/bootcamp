ThisBuild / scalaVersion := "2.12.18"
ThisBuild / organization := "com.fossilrag"
ThisBuild / version      := "0.1.0"

lazy val root = (project in file("."))
  .settings(
    name := "specimen-etl",
    Compile / scalaSource := baseDirectory.value,
    libraryDependencies ++= Seq(
      "org.apache.spark" %% "spark-core"     % "3.5.1" % Provided,
      "org.apache.spark" %% "spark-sql"      % "3.5.1" % Provided,
      "org.slf4j"         % "slf4j-api"      % "2.0.13",
      "org.scalatest"    %% "scalatest"      % "3.2.18" % Test
    )
  )
