"""Prove complete-source preservation with exhaustive, optimized input partitions."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from tools.bbc.proofs.cpu_reference import foundation, prepared
from tools.bbc.proofs.video_cursor import replace_once
from tools.validation.common import write_json


def expose_inputs(design):
    """Replace two independent free vectors with equivalent public inputs."""
    if set(design["modules"]) != {"DocumentedModelContract"}:
        raise ValueError("Expected the flattened source-preservation graph")
    module = design["modules"]["DocumentedModelContract"]
    if set(module["ports"]) != {"equivalent", "context"}:
        raise ValueError("Changed preservation outputs")
    if any(
        port["direction"] != "output" or len(port["bits"]) != 1 for port in module["ports"].values()
    ):
        raise ValueError("Changed preservation property port")
    forbidden = ("$assume", "$check", "$assert", "$anyconst", "$initstate", "$ff")
    if any(
        cell["type"] in forbidden
        or "dff" in cell["type"].lower()
        or "latch" in cell["type"].lower()
        for cell in module["cells"].values()
    ):
        raise ValueError("Preservation graph gained state or constraints")
    free = {name for name, cell in module["cells"].items() if cell["type"] == "$anyseq"}
    drivers, seen = set(), set()
    for name, width in (("state_in", 256), ("pins_in", 64)):
        bits = module["netnames"][name]["bits"]
        if (
            len(bits) != width
            or len(set(bits)) != width
            or any(type(bit) is not int for bit in bits)
            or seen.intersection(bits)
        ):
            raise ValueError("Changed source-preservation input vector")
        matched = [key for key in free if module["cells"][key]["connections"]["Y"] == bits]
        if len(matched) != 1:
            raise ValueError("Preservation inputs are not independent free vectors")
        driver = module["cells"][matched[0]]
        if (
            driver["port_directions"] != {"Y": "output"}
            or int(driver["parameters"]["WIDTH"], 2) != width
        ):
            raise ValueError("Changed symbolic preservation driver")
        drivers.update(matched)
        seen.update(bits)
        module["ports"][name] = {"direction": "input", "bits": bits}
    if drivers != free:
        raise ValueError("Extra symbolic preservation inputs")
    for name in drivers:
        del module["cells"][name]
    return design


def wrapper(kind, index):
    text = foundation.partition_wrapper(kind, index)
    for old, new in (
        ("M6502ReferenceNext", "Preservation"),
        ("output [255:0] state_out,output [63:0] pins_out,output trap", "output equivalent"),
        ("Full original", "DocumentedModelContract original"),
        (".state_out(state_out),.pins_out(pins_out),.trap(trap)", ".equivalent(equivalent)"),
    ):
        text = replace_once(text, old, new)
    return text


def qualify(graph, directory, source, *, jobs=2):
    if not 1 <= jobs <= 65:
        raise ValueError("Preservation workers must be between 1 and 65")
    directory.mkdir(exist_ok=True)
    started = time.monotonic()
    core = directory / "core.json"
    core.write_text(json.dumps(expose_inputs(json.loads(graph.read_text()))))
    cover = foundation.verify_partition_cover(source)

    def check(partition):
        kind, index = partition
        name = f"{kind}-{index:02d}"
        case = directory / name
        case.mkdir(exist_ok=True)
        property_file, mapped, log = [
            case / filename for filename in ("wrapper.v", "graph.json", "proof.log")
        ]
        property_file.write_text(wrapper(kind, index))
        command = f"read_json {core}; read_verilog {property_file}; prep -top Preservation -flatten; check -assert; opt; memory_map; opt; techmap; opt; abc -g AND; opt_clean; write_json {mapped}"
        cached = prepared.prepare(case, command, [core, property_file], [mapped], timeout=900)
        query = [
            "yosys",
            "-Q",
            "-T",
            "-p",
            f"read_json {mapped}; sat -prove equivalent 1 -verify -timeout 120",
        ]
        write_json(case / "command.json", query)
        wall = time.monotonic()
        with log.open("w") as output:
            trial = subprocess.run(query, stdout=output, stderr=subprocess.STDOUT, timeout=180)
        return {
            "name": name,
            "accepted": trial.returncode == 0
            and "SAT proof finished - no model found: SUCCESS!" in log.read_text(),
            "preparation_cache_reused": cached,
            "query_seconds": time.monotonic() - wall,
        }

    checks = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for check_result in pool.map(check, foundation.PARTITIONS):
            checks.append(check_result)
            print(
                "Complete-source preservation "
                + check_result["name"]
                + (": PASS" if check_result["accepted"] else ": FAIL"),
                flush=True,
            )
    passed = len(checks) == 65 and all(check["accepted"] for check in checks)
    result = {
        "passed": passed,
        "checks": checks,
        "cover": cover,
        "workers": jobs,
        "wall_seconds": time.monotonic() - started,
        "domain": "Read hold covers arbitrary IR/SYNC. Otherwise all 256 incoming SYNC bytes are covered. Non-SYNC execution covers every IR from 0 through 2047; the original official-context implication excludes higher IR and nonofficial instructions. Input constructors come from the independently tested complete-source foundation. The union covers the entire original context, including trapped official microsteps. All 321 compared output bits remain inside the unchanged property; no input assumptions or output masks are introduced.",
    }
    write_json(directory / "result.json", result)
    if not passed:
        raise ValueError("Complete-source partition preservation incomplete")
    return result
