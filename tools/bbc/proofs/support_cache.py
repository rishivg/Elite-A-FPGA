"""Reuse independently executed CPU comparisons after checking every bound artifact."""

import importlib

from tools.bbc import build, cpu_instruction_proof


def qualify():
    for name, module in (("cpu-arithmetic", "cpu_arithmetic"), ("cpu-values", "cpu_values")):
        path = build.BBC / name / "result.json"
        try:
            cpu_instruction_proof.supporting_evidence(path)
        except (OSError, ValueError, KeyError):
            importlib.import_module("tools.bbc." + module).qualify()
            cpu_instruction_proof.supporting_evidence(path)
        else:
            print("Verified cached independent CPU comparisons: " + name, flush=True)


if __name__ == "__main__":
    qualify()
