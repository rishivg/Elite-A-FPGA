"""Ship export accepts the checked-in face schema and rejects invalid topology."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.shipgen import main


class ShipExport(unittest.TestCase):
    def export(self, ship):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "ship.json"
            dst = Path(directory) / "ship.mem"
            src.write_text(json.dumps(ship))
            main(str(src), str(dst))
            return [int(word, 16) for word in dst.read_text().splitlines()]

    def test_shared_face_edges_are_unique_and_explicit_wire_lists_are_preserved(self):
        ship = {
            "vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
            "faces": [[0, 1, 2], [2, 1, 3]],
        }
        words = self.export(ship)
        self.assertEqual(words[13:], [5, 0x0001, 0x0002, 0x0102, 0x0103, 0x0203])
        self.assertEqual(self.export({**ship, "edges": [[3, 0], [1, 2]]})[13:], [2, 0x0300, 0x0102])

    def test_invalid_vertices_faces_and_edges_fail(self):
        ship = {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]}
        for bad in [
            {"vertices": []},
            {"vertices": [[0, 0]]},
            {"faces": [[0, 1]]},
            {"faces": [[0, 1, 3]]},
            {"edges": [[0, 0]]},
            {"edges": [[0, -1]]},
            {"edges": [[0, 1.5]]},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.export({**ship, **bad})
