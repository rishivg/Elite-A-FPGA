"""Build small native probes from exact, current generated device modules."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tools.validation.common import ROOT, digest, write_json
from tools.validation.setup import verify

from . import build


def device_rtl(names):
    """Extract unchanged module text; reject missing generated dependencies."""
    generated = (build.BBC / "BbcMachine.v").read_text()
    modules = {
        m[1]: m[0]
        for m in re.finditer(r"^module\s+(\w+)\b[\s\S]*?^endmodule\b", generated, re.MULTILINE)
    }
    if not names or len(set(names)) != len(names) or set(names) - modules.keys():
        raise ValueError("Missing or duplicate generated device module")
    selected = "\n\n".join(modules[name] for name in names) + "\n"
    for name in modules.keys() - set(names):
        if re.search(r"\b" + re.escape(name) + r"\s+(?:#\s*\(|\w+\s*\()", selected):
            raise ValueError("Missing generated module dependency: " + name)
    return "`timescale 1ns/1ps\n" + selected


def compile_probe(
    directory,
    top,
    dependencies,
    modules,
    savable=False,
    cpp_standard=17,
    *,
    cpp_defines=None,
    parameters=None,
):
    if cpp_standard not in {17, 20}:
        raise ValueError("Unsupported probe C++ language standard")
    flags = f"-O3 -std=c++{cpp_standard} -flto -Werror=return-type"
    cpp_defines = {} if cpp_defines is None else cpp_defines
    parameters = {} if parameters is None else parameters
    for name, value in [*cpp_defines.items(), *parameters.items()]:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None or type(value) is not int:
            raise ValueError("Unsupported probe definition or parameter")
    flags += "".join(f" -D{name}={value}" for name, value in sorted(cpp_defines.items()))
    verify()
    if not build.generation_current():
        raise ValueError("Run make bbc-gen before checking BBC device RTL")
    directory.mkdir(parents=True, exist_ok=True)
    rtl = directory / "devices.v"
    text = device_rtl(modules)
    if not rtl.is_file() or rtl.read_text() != text:
        rtl.write_text(text)
    files = [rtl, Path(__file__), *dependencies]
    if "Intel8271" in modules:
        files.extend([ROOT / "sim/native/bbc_fm_media.h", ROOT / "sim/native/bbc_disc_payload.h"])

    inputs = {
        "sources": build.hashes(files),
        "verilator": subprocess.check_output(["verilator", "--version"], text=True).strip(),
        "compiler": subprocess.check_output(["c++", "--version"], text=True).splitlines()[0],
        "recipe": {
            "top": top,
            "modules": modules,
            "savable": savable,
            "optimization": flags + "; OPT_FAST=-O3",
            **({"parameters": parameters} if parameters else {}),
        },
    }
    binary = directory / ("V" + top)
    try:
        receipt = json.loads((directory / "build.json").read_text())
    except (OSError, ValueError):
        receipt = {}
    if (
        not binary.is_file()
        or receipt.get("inputs") != inputs
        or receipt.get("binary") != digest(binary)
    ):
        with (directory / "build.log").open("w") as log:
            subprocess.run(
                [
                    "verilator",
                    "--cc",
                    "--exe",
                    "--build",
                    *(["--savable"] if savable else []),
                    "-j",
                    os.environ.get("VERILATOR_JOBS", "4"),
                    "-Wno-fatal",
                    "--top-module",
                    top,
                    *[f"-G{name}={value}" for name, value in sorted(parameters.items())],
                    "-CFLAGS",
                    flags,
                    "-LDFLAGS",
                    "-flto",
                    "-MAKEFLAGS",
                    "-B OPT_FAST=-O3",
                    "--Mdir",
                    str(directory),
                    *map(str, (p for p in files if p.suffix in {".v", ".cpp"})),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        if (
            not build.generation_current()
            or device_rtl(modules) != text
            or build.hashes(files) != inputs["sources"]
        ):
            raise ValueError("Device sources changed during probe compilation")
        write_json(directory / "build.json", {"inputs": inputs, "binary": digest(binary)})
    return inputs
