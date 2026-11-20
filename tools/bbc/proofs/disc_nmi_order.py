"""Exercise first-byte NMI sampling at the exact disc-read boundary."""

import json
import subprocess
from pathlib import Path

from tools.bbc import bus_binding_proof
from tools.bbc.proofs import current_machine, disc_clocking
from tools.bbc.proofs.video_cursor import hashes, replace_once
from tools.validation.common import ROOT, source_root, write_json

DIRECTORY = ROOT / "build/bbc/disc-nmi-order"


def qualify():
    current_machine.audit()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    original = source_root("jsbeeb")
    disc = ROOT / "build/bbc/disc.ssd"
    reference = Path(__file__).with_name("reference_disc_nmi_order.mjs")
    parents = current_machine.parents()
    files = [
        Path(__file__),
        reference,
        disc,
        Path(current_machine.__file__),
        Path(disc_clocking.__file__),
        disc_clocking.DRIVER,
        *disc_clocking.HEADERS,
        *[p for _, p, _ in parents],
        *[original / "src" / name for name in ("intel-fdc.js", "scheduler.js", "disc-drive.js")],
        *[
            ROOT / "tools/bbc" / name
            for name in ("reference_disc_media_base.mjs", "reference_drive.mjs")
        ],
    ]
    before = hashes(files)
    observations = DIRECTORY / "original.json"
    subprocess.run(["node", reference, original, disc, observations], check=True, timeout=60)
    cases = json.loads(observations.read_text())
    expected = [(0, 0, 0), (0, 1, 0), (1, 1, 0)]
    if [(c["previous"], c["beforeRead"], c["afterRead"]) for c in cases] != expected:
        raise ValueError("Original disc corpus did not exercise the coincident NMI edge")
    fixtures = [DIRECTORY / c["fixture"] for c in cases]
    anchors = hashes([observations, *fixtures])
    rows, artifacts = [], [observations, *fixtures]
    for multiplier, path, top in parents:
        source = path.read_text()
        parent = bus_binding_proof.module(source, top)
        fdc = bus_binding_proof.module(source, "Intel8271")
        driver, enable = disc_clocking.DRIVER.read_text(), None
        if multiplier == 3:
            driver, enable = disc_clocking.driver96(driver, parent, fdc)
        fdc = replace_once(
            fdc, "module Intel8271 (", "module Intel8271 (output wire [1:0] proofNmi,"
        )
        fdc = replace_once(fdc, "endmodule", "assign proofNmi={nmiLevel,eventNmi};\nendmodule")
        gate = f" && v.{enable}" if enable else ""
        driver = replace_once(
            driver,
            "            auto edge = [&]() {",
            """            bool sampledNmi = false;
            auto edge = [&]() {""",
        )
        center = cases[1]["epoch"] * 16 * multiplier
        driver = replace_once(
            driver,
            "                v.clk = 1;",
            f"""                if (!v.reset) {{
                    if (v.io_nmi && !sampledNmi && clock + 1 >= {center - 96} && clock + 1 <= {center + 96})
                        std::cout << "rise " << name << " " << (clock + 1) << '\\n';
                    if (v.io_read && v.io_address == 4{gate})
                        std::cout << "edge " << name << " " << (clock + 1) << " "
                                  << unsigned(v.proofNmi) << " " << unsigned(v.io_nmi) << '\\n';
                    sampledNmi = v.io_nmi;
                }}
                v.clk = 1;""",
        )
        result_dir = DIRECTORY / f"clock{multiplier}"
        binary = disc_clocking.compile_model(result_dir, fdc, driver)
        replay = disc_clocking.replay(
            binary, list(zip(cases, fixtures, strict=True)), result_dir, path.parent, False
        )
        if not disc_clocking.passed(replay):
            raise ValueError("Disc bus/storage observations differ before NMI ordering check")
        expected_by_name = {c["name"]: entry for c, entry in zip(cases, expected, strict=True)}
        samples, rises = {}, {}
        for line in replay["execution"].splitlines():
            if line.startswith("rise "):
                _, name, physical = line.split()
                rises.setdefault(name, []).append(int(physical))
                continue
            if not line.startswith("edge "):
                continue
            _, name, physical, before_edge, cpu_pin = line.split()
            if name in samples or name not in expected_by_name:
                raise ValueError("Invalid actual CPU pin sample boundary")
            before_edge = int(before_edge)
            previous, prior_read, after_read = expected_by_name[name]
            if (before_edge & 1, int(cpu_pin)) != (prior_read, after_read):
                raise ValueError("Disc callback/ack endpoints differ from Original")
            samples[name] = {
                "reference_rise_in_read_slot": not previous and bool(prior_read),
                "actual_read_edge": int(physical),
                "actual_final_cpu_pin": int(cpu_pin),
                "actual_rises_before_read": [e for e in rises.get(name, []) if e < int(physical)],
            }
        if set(samples) != set(expected_by_name):
            raise ValueError("Incomplete actual disc pin observation")
        coincident = samples["offset-0"]
        if len(coincident["actual_rises_before_read"]) != 1 or not (
            0
            < coincident["actual_read_edge"] - coincident["actual_rises_before_read"][0]
            < 16 * multiplier
        ):
            raise ValueError("CPU pin lost or retimed the NMI raised in the coincident disc slot")
        rows.append({"clock_multiplier": multiplier, "cases": samples})
        artifacts.extend(
            p
            for p in result_dir.iterdir()
            if p.is_file() and p.suffix in {".log", ".tsv", ".v", ".cpp"}
        )
    if before != hashes(files) or anchors != hashes([observations, *fixtures]):
        raise ValueError("Disc NMI check inputs changed")
    write_json(
        DIRECTORY / "result.json",
        {
            "passed": True,
            "inputs_unchanged": True,
            "inputs": before,
            "artifacts": hashes(artifacts),
            "clocks": rows,
            "scope": "Actual generated drive controller with each parent clock-enable phase, separately cold pinned Original and RTL. The first byte arrives on the same reference CPU slot as a data read. The RTL CPU pin sees a physical rise before the acknowledgement; the final sampled levels and bus reads agree.",
            "limits": "This checks one first-byte race at three offsets. It does not establish controller/CPU interrupt age, all callback collisions or full game equivalence.",
        },
    )
    print("FDC first-byte NMI survives same-slot acknowledgement at 32 and 96 MHz", flush=True)


if __name__ == "__main__":
    qualify()
