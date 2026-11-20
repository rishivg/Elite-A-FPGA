# Proof baselines

These are exact source snapshots used by the audio, VIA, system-pin and video
preservation proofs. Each directory is named for the original commit, and
`manifest.json` pins every file's SHA-256. The proof loader checks the digest
before returning a source file.

The snapshots keep these checks reproducible from a shallow or single-commit
checkout. They are proof inputs, not additional production RTL. Do not format
or update them to match the current implementation.

The files live outside sbt source roots to keep their historical class names
out of production and formal builds.
