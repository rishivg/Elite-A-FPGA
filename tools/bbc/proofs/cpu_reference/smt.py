"""Run fresh word-level source-history proofs with explicit initialization."""

import json
import os
import resource
import shutil
import subprocess
import time
from pathlib import Path

from tools.bbc.proofs.cpu_reference import prepared
from tools.validation.common import ROOT


def tools():
    driver = shutil.which("yosys-smtbmc")
    solver = shutil.which("z3") or str(ROOT / "build/bbc/formal-tools/bin/z3")
    if not driver or not Path(solver).is_file():
        raise ValueError("Source history SMT checks require yosys-smtbmc and Z3")
    data = Path(driver).resolve().parent.parent / "share/yosys"
    if not (data / "smtio.py").is_file():
        raise ValueError("Missing installed Yosys SMT Python helper")
    return Path(driver), Path(solver), data / "smtio.py"


def guard_initialization(graph, top):
    """Match SAT's set-init-zero without silently narrowing symbolic inputs.

    Require every FF bit to have an explicit, defined initial attribute. The
    source histories currently meet this condition; graphs with uninitialized
    FFs must use the SAT path until their initialization is resolved explicitly.
    No attribute or next-state driver is changed by this check.
    """
    module = graph["modules"][top]
    registered = set()
    for cell in module["cells"].values():
        if "ff" in cell["type"].lower():
            if cell["type"] not in ("$dff", "$dffe", "$sdff", "$sdffe"):
                raise ValueError("Unsupported source state cell: " + cell["type"])
            registered.update(cell["connections"]["Q"])
    initialized = {}
    for net in module["netnames"].values():
        value = net["attributes"].get("init")
        if value is not None:
            for bit, digit in zip(net["bits"], value[::-1], strict=True):
                # Optimized aliases retain X init markers for removed wires
                # and constant bits. They are not storage initialization.
                if type(bit) is not int or digit == "x" and bit not in registered:
                    continue
                if digit not in ("0", "1") or (bit in initialized and initialized[bit] != digit):
                    raise ValueError("Undefined or conflicting source initialization")
                initialized[bit] = digit
    for cell in module["cells"].values():
        if (
            cell["type"] in ("$anyconst", "$anyseq")
            and set(cell["connections"]["Y"]) & initialized.keys()
        ):
            raise ValueError("A symbolic source input acquired initialization")
    if any(bit not in initialized for bit in registered):
        raise ValueError("SMT path requires explicitly initialized source FFs")
    return len(registered)


def prove(work, graph, top, *, steps=15, timeout=180):
    if type(steps) is not int or steps < 1 or type(timeout) is not int or timeout < 1:
        raise ValueError("Source SMT depth and timeout must be positive integers")
    driver, solver, helper = tools()
    register_bits = guard_initialization(json.loads(graph.read_text()), top)
    work.mkdir(exist_ok=True)
    wrapper, model, formal = work / "wrapper.v", work / "property.smt2", work / "formal.json"
    wrapper.write_text(
        "module SourceHistorySmt(input clk);wire equivalent;\n"
        f"{top} actual(.clk(clk),.equivalent(equivalent));\n"
        "always @* assert(equivalent);\nendmodule\n"
    )
    command = f"read_json {graph}; read_verilog -formal {wrapper}; prep -top SourceHistorySmt -flatten; check -assert; chformal -lower; dffunmap; opt_clean; write_json {formal}; write_smt2 -wires {model}"
    cached = prepared.prepare(work, command, [graph, wrapper], [formal, model], timeout=600)
    module = json.loads(formal.read_text())["modules"]["SourceHistorySmt"]
    assertions = [c for c in module["cells"].values() if c["type"] == "$assert"]
    if (
        len(assertions) != 1
        or assertions[0]["connections"]
        != {"A": module["netnames"]["equivalent"]["bits"], "EN": ["1"]}
        or any(c["type"] == "$check" for c in module["cells"].values())
    ):
        raise ValueError("SMT graph lost the unconditional equivalence assertion")
    if model.read_text().count("; yosys-smt2-assert ") != 1:
        raise ValueError("SMT export lost the equivalence assertion")
    environment = {
        **os.environ,
        "PATH": str(solver.parent) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(helper.parent) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    checks, artifacts = [], [wrapper, formal, model, work / "prepared.json", work / "prepare.log"]
    for name, mode in (("base", []), ("induction", ["-i"])):
        log, witness = work / (name + ".log"), work / (name + "-witness.vcd")
        witness.unlink(missing_ok=True)
        cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.perf_counter()
        timed_out = False
        with log.open("w") as output:
            try:
                trial = subprocess.run(
                    [
                        str(driver),
                        "-s",
                        "z3",
                        *mode,
                        "-t",
                        str(steps),
                        "--noprogress",
                        "--timeout",
                        str(timeout),
                        "--dump-vcd",
                        str(witness),
                        str(model),
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    timeout=timeout + 60,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                trial = None
                output.write("\nSource SMT process exceeded its wall-time budget.\n")
        elapsed = time.perf_counter() - started
        cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        passed = not timed_out and trial.returncode == 0 and "Status: PASSED" in log.read_text()
        if name == "induction":
            passed = passed and "Temporal induction successful." in log.read_text()
        checks.append(
            {
                "name": name,
                "passed": passed,
                "steps": steps,
                "elapsed_seconds": elapsed,
                "solver_cpu_seconds": cpu_after.ru_utime
                + cpu_after.ru_stime
                - cpu_before.ru_utime
                - cpu_before.ru_stime,
            }
        )
        artifacts.append(log)
        if witness.exists():
            artifacts.append(witness)
        print(f"Source SMT {name}: " + ("PASS" if passed else "FAIL"), flush=True)
        if not passed:
            break
    return {
        "passed": len(checks) == 2 and all(c["passed"] for c in checks),
        "checks": checks,
        "register_bits": register_bits,
        "preparation_cache_reused": cached,
    }, artifacts
