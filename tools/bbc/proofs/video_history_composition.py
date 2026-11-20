"""Seal bitmap/teletext history premises and their actual cell bindings."""

from pathlib import Path

from tools.bbc import build, bus_binding_proof
from tools.bbc.proofs import memory_composition
from tools.bbc.proofs.video_cursor import hashes
from tools.validation.common import ROOT, digest, write_json


def qualify():
    names = (
        "ram-proof",
        "video-memory-response-deadline-proof",
        "peripheral-pin-binding-proof",
        "video32-register-contract",
        "video96-register-contract",
        "video32-palette-contract",
        "video-palette-contract",
        "video32-cursor-segment-contract",
        "video-cursor-segment-contract",
        "crtc-history-contract",
        "bitmap32-history-contract",
        "bitmap96-history-contract",
        "teletext-attribute-history-contract",
        "teletext-font-response-contract",
        "teletext-glyph-contract",
        "teletext-font-consumption-deadline",
        "teletext-memory-response-deadline",
        "teletext-scanout-contract",
        "teletext-input-placement-contract",
    )
    paths = {name: build.BBC / name / "result.json" for name in names}
    reports = {name: memory_composition.sealed(path) for name, path in paths.items()}
    parents = [build.BBC / "BbcMachine.v", build.BBC / "full-video/BbcMachine96.v"]
    for name, report in reports.items():
        required = (
            parents[:1]
            if name.startswith(("video32-", "bitmap32-"))
            else parents[1:]
            if name.startswith(("video96-", "bitmap96-", "teletext-"))
            or name in {"video-palette-contract", "video-cursor-segment-contract"}
            else parents
        )
        for parent in required:
            key = str(parent.relative_to(ROOT))
            if report["inputs"].get(key, report["inputs"].get(str(parent))) != digest(parent):
                raise ValueError("Video premise binds a different parent: " + name)
    for name, count in (
        ("crtc-history-contract", 106),
        ("teletext-attribute-history-contract", 77),
        ("teletext-font-response-contract", 14),
        ("teletext-scanout-contract", 48),
    ):
        if reports[name]["register_bits"] != count:
            raise ValueError("Changed video state inventory")
    full = parents[1].read_text()
    cell = bus_binding_proof.module(full, "Saa5050Cell")
    attributes = bus_binding_proof.ports(cell, "TeletextAttributes", "attributes")
    font = bus_binding_proof.ports(cell, "TeletextFont", "font")
    glyph = bus_binding_proof.ports(cell, "TeletextGlyphRow", "glyph")
    equalities = [
        (font["io_character"], attributes["io_character"]),
        (font["io_raster"], attributes["io_glyphRaster"]),
        (glyph["io_character"], attributes["io_character"]),
        (glyph["io_raster"], attributes["io_glyphRaster"]),
        (glyph["io_glyphSet"], attributes["io_glyphSet"]),
        (glyph["io_alpha"], font["io_alpha"]),
        (glyph["io_neighbour"], font["io_neighbour"]),
    ]
    if any(a.replace(" ", "") != b.replace(" ", "") for a, b in equalities):
        raise ValueError("Changed actual cell composition wiring")
    for required in (
        "assign io_dots = (attributes_io_suppressed ? 12'h0 : glyph_io_dots);",
        "assign io_foreground = attributes_io_foreground;",
        "assign io_background = attributes_io_background;",
    ):
        if required not in cell:
            raise ValueError("Changed cell output/suppression binding")
    files = [
        Path(__file__),
        Path(memory_composition.__file__),
        Path(bus_binding_proof.__file__),
        Path(__file__).with_name("video_cursor.py"),
        *paths.values(),
        *parents,
    ]
    before = hashes(files)
    for path in paths.values():
        memory_composition.sealed(path)
    stable = before == hashes(files)
    write_json(
        build.BBC / "video-history-composition/result.json",
        {
            "passed": stable,
            "inputs": before,
            "inputs_unchanged": stable,
            "premises": {name: str(path.relative_to(ROOT)) for name, path in paths.items()},
            "derivation": [
                "The same-parent bitmap history contracts join the declared CRTC, register, palette, physical master/character scheduling, held placement/display, cursor and RAM-address rules over arbitrary actual video input histories. The earlier datapath premises separately connect original public local rules and initialized memory contents.",
                "Watched-word RAM semantics and the actual slow-input deadline supply a completed retained byte to the IC15 acceptance. The input/placement contract supplies the MA13 latch, bit-six blanking, character/DEW/LOSE/RA0 operations and captured coordinates for the declared CRTC histories.",
                "The integrated attribute history theorem supplies current character, glyph selection, raster, suppression and cell colours for those operation histories. The source-font theorem supplies both preceding-edge rows from the exact original ROM image.",
                "The actual font-consumption deadline supplies unchanged current/preceding-edge character/raster inputs at each scanout load, after a character update. Exact cell port bindings therefore connect those ROM rows to the current glyph. The complete combinational glyph theorem supplies every dot and resampling weight, and the actual cell suppression/colour drivers are checked.",
                "The actual scanout theorem admits these free cell/load/placement values and supplies held colour/dot/coordinate state, exact twelve-dot serialization, pixel/display/RGB and cursor inversion while TT is selected. Its input boundaries are discharged by the separately proved producer and consumption contracts at the actual load guard.",
            ],
            "scope": "Same-source composition of nineteen current memory, bitmap and teletext digital contracts at the actual video component boundaries, with exact integrated cell port/suppression/colour bindings. Every premise/artifact seal is checked before and after. This closes the stated local digital equations and data/timing dependencies; it is not a whole-machine theorem.",
            "limits": "Declared digital CRTC/ULA/teletext equations. A universal mapping to independent Original CPU/video/device algorithms, absolute reference phase, the Original four-byte pipeline versus IC15 plus three stages, event/MMIO timing and global reachability remain unresolved. Fast teletext tricks and NULA are outside the rendering domain. The current parents retain the baseline keypad child; source repair regeneration/native activation remains separate. No universal game equivalence.",
            "universal_game_equivalence": False,
        },
    )
    if not stable:
        raise ValueError("Video history composition inputs changed")


if __name__ == "__main__":
    qualify()
