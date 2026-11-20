"""Reject false epoch origins, bypassed bus strobes and narrowed gap proofs."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.validation.test_c_callback_order import SOURCE
from tools.bbc.proofs.cpu_reference import callback_order, device_epoch, fdc_epoch


class DeviceEpochTests(unittest.TestCase):
    def test_changed_payload_header_invalidates_native_build_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model, driver, header, tool = [
                directory / name for name in ("actual.v", "driver.cpp", "payload.h", "tool")
            ]
            for path in (model, driver, header, tool):
                path.write_text("first")

            def build(*args, **kwargs):
                (directory / "VProbe").write_text("compiled")

            with (
                patch.object(callback_order.shutil, "which", return_value=str(tool)),
                patch.object(callback_order.subprocess, "run", side_effect=build) as compiler,
            ):
                callback_order.compile_native(model, directory, "Probe", driver, [header])
                callback_order.compile_native(model, directory, "Probe", driver, [header])
                self.assertEqual(compiler.call_count, 1)
                header.write_text("changed encoding")
                callback_order.compile_native(model, directory, "Probe", driver, [header])
                self.assertEqual(compiler.call_count, 2)

    def test_native_probe_keeps_real_acceptance_decoder_and_data_bindings(self):
        source = SOURCE.replace(
            ".io_nmi(discNmi)",
            ".io_nmi(discNmi),.io_read(io_fdcRead),.io_write(io_fdcWrite),"
            ".io_address(io_fdcAddress[2:0]),.io_data(cpu_write_data[7:0])",
        ).replace("Intel8271 fdc (", "Intel8271 intel8271_1 (")
        source = source.replace(
            "endmodule",
            """
assign io_fdcRead = (read && decode_io_fdc);
assign io_fdcWrite = (write && decode_io_fdc);
assign read = (cpuEnable && (! cpu_write_enable));
assign write = (cpuEnable && cpu_write_enable);
assign io_fdcAddress = cpu_address[2 : 0];
endmodule
""",
            1,
        )
        fdc_epoch.binding(source, "Parent")
        for altered in (
            source.replace("read && decode_io_fdc", "tickCpu && decode_io_fdc"),
            source.replace(".io_data(cpu_write_data[7:0])", ".io_data(unrelatedData[7:0])"),
            source.replace(".nmi(discNmi)", ".nmi(unrelatedNmi)"),
        ):
            with self.subTest(source=altered), self.assertRaises(ValueError):
                fdc_epoch.binding(altered, "Parent")

    def test_shared_reset_duration_and_peripheral_before_bus_order_are_guarded(self):
        reference = (
            'powerProfile === "shared-reset"\ncpu.currentCycles += 16;\ncpu.scheduler.polltime(16);'
        )
        driver = "const uint64_t cycle = master / BBC_MASTER_MULTIPLIER;\ndut.reset = !restoring && cycle < 256;\nbus << (cycle + 1)"
        adapter = "cpu.polltime(1);\nslots++;\nif (--remaining === 0)\nengine.tick(pins)"
        device_epoch.guard_protocol(reference, driver, adapter)
        for texts in (
            (reference.replace("+= 16", "+= 15"), driver, adapter),
            (reference, driver.replace("< 256", "< 255"), adapter),
            (reference, driver.replace("cycle + 1", "cycle"), adapter),
            (
                reference,
                driver,
                adapter.replace("slots++;", "").replace(
                    "cpu.polltime(1);", "slots++;\ncpu.polltime(1);"
                ),
            ),
            (reference, driver, adapter + "\nengine.tick(pins)"),
        ):
            with self.subTest(texts=texts), self.assertRaises(ValueError):
                device_epoch.guard_protocol(*texts)

    def test_fault_fixture_requires_the_complete_late_service_case_and_inventory(self):
        names = ["early", "two-slots", "last-slot-read", "alias", "write", "write-alias", "result"]
        parts = [f"case {i} {name} 1\n17 P 0 0 0 0 0\n" for i, name in enumerate(names)]
        source = "".join(parts)
        self.assertEqual(fdc_epoch.fault_fixture(source), parts[2].replace("case 2 ", "case 0 "))
        for bad in (
            source + parts[2],
            source.replace("last-slot-read", "other"),
            "".join(parts[:-1]),
            source[:-2],
        ):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                fdc_epoch.fault_fixture(bad)

    def gap_graph(self):
        cells = {
            "initial": {"type": "$initstate", "connections": {"Y": [4]}},
            "reset": {"type": "$assume", "connections": {"A": [3], "EN": [4]}},
        }
        for name, bit in [("eventNmi", 10), ("commandNmi", 11), ("abortNmi", 14)]:
            cells[name] = {"type": "$anyseq", "connections": {"Y": [bit]}}
        for name, bit in [("actualGap", 12), ("monitor", 13)]:
            cells[name] = {
                "type": "$sdff",
                "connections": {"CLK": [2], "Q": [bit]},
                "parameters": {"CLK_POLARITY": "1"},
            }
        return {
            "modules": {
                "Intel8271": {
                    "ports": {"clk": {"bits": [2]}, "reset": {"bits": [3]}},
                    "cells": cells,
                    "netnames": {
                        "eventNmi": {"bits": [10]},
                        "commandNmi": {"bits": [11]},
                        "abortNmi": {"bits": [14]},
                    },
                }
            }
        }

    def test_gap_keeps_actual_storage_and_free_event_stages(self):
        device_epoch.guard_gap(self.gap_graph())

    def test_gap_domain_cannot_assume_quiet_events_or_remove_actual_state(self):
        for fault in ("assumption", "enable", "constant-event", "missing-state", "wrong-clock"):
            graph = copy.deepcopy(self.gap_graph())
            cells = graph["modules"]["Intel8271"]["cells"]
            if fault == "assumption":
                cells["quiet"] = {"type": "$assume"}
            elif fault == "enable":
                cells["reset"]["connections"]["EN"] = [5]
            elif fault == "constant-event":
                cells["eventNmi"]["type"] = "$anyconst"
            elif fault == "missing-state":
                del cells["actualGap"]
            else:
                cells["actualGap"]["connections"]["CLK"] = [5]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                device_epoch.guard_gap(graph)


if __name__ == "__main__":
    unittest.main()
