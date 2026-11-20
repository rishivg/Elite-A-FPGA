"""Physical keyboard schedules derived from the verified reference key map."""

import re

from tools.validation.common import source_root


def key_matrix():
    source = (source_root("jsbeeb") / "src/keymap.js").read_text()
    section = source.split("export const BBC = {", 1)[1].split("};", 1)[0]
    return {
        name: (int(col), int(row))
        for name, col, row in re.findall(r"(\w+): \[(\d+), (\d+)\]", section)
    }


def transitions(scenario):
    matrix = key_matrix()
    result = []
    previous = set()
    last = -1
    for event in scenario.get("events", []):
        at = event["at_us"]
        current = set(event["keys"])
        if (
            not isinstance(at, int)
            or not last <= at < scenario["duration_us"]
            or len(current) != len(event["keys"])
            or not current <= matrix.keys()
        ):
            raise ValueError("Invalid physical keyboard event")
        for down, keys in [(0, previous - current), (1, current - previous)]:
            for key in sorted(keys):
                result.append([at * 32, *matrix[key], down])
        previous = current
        last = at
    return result


def analogue_transitions(scenario):
    """Four unsigned 16-bit ADC inputs and two independent FIRE pins."""
    result = []
    previous = -1
    for event in scenario.get("analogue", []):
        at = event.get("at_us")
        channels = event.get("channels")
        buttons = event.get("buttons")
        if (
            type(at) is not int
            or not previous < at < scenario["duration_us"]
            or at < 0
            or not isinstance(channels, list)
            or len(channels) != 4
            or any(type(v) is not int or not 0 <= v <= 65535 for v in channels)
            or type(buttons) is not int
            or not 0 <= buttons <= 3
        ):
            raise ValueError("Invalid physical analogue event")
        result.append([at * 32, *channels, buttons])
        previous = at
    return result


def write_schedule(directory, scenario, *, analogue=False):
    """Write replay fixture times in 32 MHz ticks for either machine clock."""
    schedules = {"keys.tsv": transitions(scenario)}
    if analogue:
        schedules["analogue.tsv"] = analogue_transitions(scenario)
    for name, rows in schedules.items():
        (directory / name).write_text("".join(" ".join(map(str, row)) + "\n" for row in rows))
    (directory / "checkpoints.tsv").write_text(
        "".join(
            f"{checkpoint['at_us'] * 32} {checkpoint['name']}\n"
            for checkpoint in scenario["checkpoints"]
        )
    )
