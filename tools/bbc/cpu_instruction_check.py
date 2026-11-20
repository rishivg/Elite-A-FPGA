"""Refresh stale supporting CPU evidence, then check the bounded RTL proofs."""

from pathlib import Path

from tools.validation.common import digest, write_json

from . import build, cpu_arithmetic, cpu_brk, cpu_instruction_proof, cpu_sequences, prepare


def ensure_component(path, execute):
    """Keep unchanged independent results; regenerate missing or changed ones."""
    try:
        cpu_instruction_proof.supporting_evidence(path)
    except (OSError, ValueError, KeyError, TypeError):
        execute()
        cpu_instruction_proof.supporting_evidence(path)
        return "refreshed"
    return "verified-unchanged"


def main():
    prepare.main()
    components = (
        ("cpu-brk", cpu_brk.qualify),
        ("cpu-sequences", cpu_sequences.qualify),
        ("cpu-arithmetic", cpu_arithmetic.qualify),
        ("cpu-decimal-transitions", lambda: cpu_arithmetic.qualify(transitions=True)),
    )
    checked = {}
    for name, execute in components:
        checked[name] = ensure_component(build.BBC / name / "result.json", execute)
        print(name + ": " + checked[name], flush=True)
    proof = cpu_instruction_proof.qualify()
    write_json(
        build.BBC / "cpu-instruction-check/result.json",
        {
            "passed": proof["passed"] is True,
            "components": checked,
            "producer_sha256": digest(Path(__file__)),
            "proof_receipt_sha256": digest(build.BBC / "cpu-instruction-proof/result.json"),
            "scope": "Dispatch receipt only. Missing or stale independent CPU evidence executes again; unchanged producers and artifact bytes are verified before reusing the bounded instruction proof. Universal CPU/game equivalence remains unproven.",
        },
    )


if __name__ == "__main__":
    main()
