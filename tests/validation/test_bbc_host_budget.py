"""Real process contention and inherited capture-lease lifetime checks."""

import select
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path

from tools.bbc.host_budget import capture_slot
from tools.validation.common import ROOT

WORKER = """
import sys
from tools.bbc.host_budget import capture_slot
print('waiting', flush=True)
with capture_slot(sys.argv[1], int(sys.argv[2])):
    print('acquired', flush=True)
    sys.stdin.buffer.read(1)
"""


class HostBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def process(self, script, *arguments):
        process = subprocess.Popen(
            [sys.executable, "-c", script, *map(str, arguments)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in [process.stdin, process.stdout, process.stderr]:
            stream.close()

    def line(self, process, expected):
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "Capture worker did not reach the declared endpoint")
        self.assertEqual(process.stdout.readline().decode().strip(), expected)

    @staticmethod
    def release(process):
        process.stdin.write(b"x")
        process.stdin.flush()
        if process.wait(timeout=5) != 0:
            raise AssertionError(process.stderr.read().decode())

    def test_separate_processes_share_capacity_and_queue_without_oversubscription(self):
        workers = [self.process(WORKER, self.directory, 2) for _ in range(3)]
        for worker in workers:
            self.line(worker, "waiting")
        # Acquisition order is deliberately unconstrained.
        ready, _, _ = select.select([worker.stdout for worker in workers], [], [], 5)
        acquired = []
        for worker in workers:
            if worker.stdout in ready:
                self.line(worker, "acquired")
                acquired.append(worker)
        if len(acquired) == 1:
            remaining = [worker for worker in workers if worker not in acquired]
            ready, _, _ = select.select([worker.stdout for worker in remaining], [], [], 5)
            self.assertEqual(len(ready), 1)
            worker = next(worker for worker in remaining if worker.stdout in ready)
            self.line(worker, "acquired")
            acquired.append(worker)
        self.assertEqual(len(acquired), 2)
        queued = next(worker for worker in workers if worker not in acquired)
        self.assertFalse(select.select([queued.stdout], [], [], 0.1)[0])
        self.release(acquired[0])
        self.line(queued, "acquired")
        self.release(queued)
        self.release(acquired[1])

    def test_can_use_more_than_four_available_slots(self):
        with ExitStack() as leases:
            descriptors = [leases.enter_context(capture_slot(self.directory, 5)) for _ in range(5)]
            self.assertEqual(len(set(descriptors)), 5)

    def test_child_keeps_lease_after_wrapper_closes_its_descriptor(self):
        script = "import sys; print('child-ready', flush=True); sys.stdin.buffer.read(1)"
        with capture_slot(self.directory, 1) as descriptor:
            child = subprocess.Popen(
                [sys.executable, "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(descriptor,),
                bufsize=0,
            )
            self.addCleanup(self.stop, child)
            self.line(child, "child-ready")
        queued = self.process(WORKER, self.directory, 1)
        self.line(queued, "waiting")
        self.assertFalse(select.select([queued.stdout], [], [], 0.1)[0])
        self.release(child)
        self.line(queued, "acquired")
        self.release(queued)

    def test_failed_owner_does_not_leave_a_stale_permit(self):
        with (
            self.assertRaisesRegex(RuntimeError, "backend failed"),
            capture_slot(self.directory, 1),
        ):
            raise RuntimeError("backend failed")
        worker = self.process(WORKER, self.directory, 1)
        self.line(worker, "waiting")
        self.line(worker, "acquired")
        self.release(worker)

    def test_backend_timeout_starts_after_host_queue(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
from tools.bbc.capture import run
from tools.bbc.host_budget import capture_slot
print('waiting', flush=True)
with patch('tools.bbc.capture.capture_slot', lambda: capture_slot(sys.argv[1], 1)):
    run([sys.executable, '-c', 'pass'], Path.cwd(), Path(sys.argv[1])/'backend.log', timeout=1)
print('completed', flush=True)
"""
        with capture_slot(self.directory, 1):
            worker = self.process(script, self.directory)
            self.line(worker, "waiting")
            self.assertFalse(select.select([worker.stdout], [], [], 1.1)[0])
        self.line(worker, "completed")
        self.assertEqual(worker.wait(timeout=5), 0)

    def wait_for_tickets(self, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(list(self.directory.glob("waiting-*"))) == count:
                return
            time.sleep(0.01)
        self.fail("Capture workers did not enqueue their requests")

    def test_waiters_acquire_in_enqueue_order(self):
        with capture_slot(self.directory, 1):
            first = self.process(WORKER, self.directory, 1)
            self.line(first, "waiting")
            self.wait_for_tickets(1)
            second = self.process(WORKER, self.directory, 1)
            self.line(second, "waiting")
            self.wait_for_tickets(2)
        self.line(first, "acquired")
        self.assertFalse(select.select([second.stdout], [], [], 0.1)[0])
        self.release(first)
        self.line(second, "acquired")
        self.release(second)
        self.assertFalse(list(self.directory.glob("waiting-*")))

    def test_killed_head_waiter_does_not_stall_queue(self):
        with capture_slot(self.directory, 1):
            first = self.process(WORKER, self.directory, 1)
            self.line(first, "waiting")
            self.wait_for_tickets(1)
            second = self.process(WORKER, self.directory, 1)
            self.line(second, "waiting")
            self.wait_for_tickets(2)
            first.kill()
            first.wait(timeout=5)
        self.line(second, "acquired")
        self.release(second)
        self.assertFalse(list(self.directory.glob("waiting-*")))
