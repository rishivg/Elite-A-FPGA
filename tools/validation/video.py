"""Full RTL presentation, input, audio wiring, and reset verification."""

import datetime
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .common import CACHE, ROOT, decode_dut, digest, firmware_symbols, publish_latest, write_json


def main():
    root = CACHE / "video-runs"
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(
        tempfile.mkdtemp(
            prefix=datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ-"),
            dir=root,
        )
    )
    subprocess.run(
        [str(ROOT / "build/rtl/obj_validate_video/validate_video"), str(directory)],
        cwd=ROOT / "build/rtl",
        check=True,
        timeout=120,
    )
    symbols = firmware_symbols()
    states = {p.stem: decode_dut(p.read_bytes(), symbols) for p in directory.glob("*.ram")}
    for name, state in states.items():
        write_json(directory / (name + ".state.json"), state)
    for p in directory.glob("*.pgm"):
        with Image.open(p) as image:
            image.save(p.with_suffix(".png"))
    checks = {
        "status_input_accepted": states["final"]["internal"]["status_mode"] == 1,
        "reset_restores_status": states["reboot"]["internal"]["status_mode"] == 0,
    }
    result = {
        **json.loads((directory / "capture.json").read_text()),
        "behavior_assertions": checks,
        "rtl_sha256": digest(ROOT / "build/rtl/EliteTop.v"),
        "firmware_sha256": digest(ROOT / "build/fw/firmware.elf"),
        "scope": "25 MHz single-domain digital integration; 640x480 RGB; no analog/physical board equivalence",
    }
    result["passed"] = all(checks.values()) and result["passed"]
    write_json(directory / "result.json", result)
    publish_latest(CACHE / "video-latest", directory)
    print(f"Full digital output: {'PASS' if result['passed'] else 'FAIL'}; {directory}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
