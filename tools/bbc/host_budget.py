"""Share the host CPU budget across independent BBC replay workspaces."""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def capacity():
    """Honor CPU affinity and the Linux container quota."""
    available = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    )
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            available = min(available, max(1, int(quota) // int(period)))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return max(1, available)


def _live_waiters(directory):
    """Prune abandoned tickets by their descriptor locks, without PID reuse races."""
    live = []
    for ticket in sorted(directory.glob("waiting-*")):
        try:
            descriptor = os.open(ticket, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                live.append(ticket)
            else:
                ticket.unlink(missing_ok=True)
        finally:
            os.close(descriptor)
    return live


@contextmanager
def capture_slot(directory=None, slots=None):
    """Allocate permits in enqueue order, retaining child-held lease lifetime.

    The descriptor is passed to the child so a killed wrapper cannot release
    the permit while its backend still runs. Queue time precedes the backend's
    wall timeout and never changes guest clocks, keys or observations. Waiting
    tickets have descriptor locks too; a killed waiter cannot stall the queue.
    Older clients still share the same slot locks, but do not participate in
    the FIFO protocol.
    """
    count = capacity() if slots is None else slots
    if type(count) is not int or count < 1:
        raise ValueError("Host capture capacity must be a positive integer")
    directory = (
        Path(tempfile.gettempdir()) / f"fpgaleet-bbc-capture-{os.getuid()}"
        if directory is None
        else Path(directory)
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    queue = ticket_descriptor = permit = None
    ticket = None
    try:
        queue = os.open(
            directory / "queue.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
        )
        fcntl.flock(queue, fcntl.LOCK_EX)
        try:
            # The host monotonic clock orders requests across processes. Create
            # and lock the ticket before exposing it to another queue scanner.
            ticket_descriptor, filename = tempfile.mkstemp(
                prefix=f"waiting-{time.monotonic_ns():020d}-", dir=directory
            )
            ticket = Path(filename)
            fcntl.flock(ticket_descriptor, fcntl.LOCK_EX)
        finally:
            fcntl.flock(queue, fcntl.LOCK_UN)
        while permit is None:
            fcntl.flock(queue, fcntl.LOCK_EX)
            try:
                waiters = _live_waiters(directory)
                if waiters and waiters[0] == ticket:
                    for index in range(count):
                        descriptor = os.open(
                            directory / str(index),
                            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                            0o600,
                        )
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            os.close(descriptor)
                            continue
                        except BaseException:
                            os.close(descriptor)
                            raise
                        permit = descriptor
                        ticket.unlink()
                        break
            finally:
                fcntl.flock(queue, fcntl.LOCK_UN)
            if permit is None:
                time.sleep(0.05)
        yield permit
    finally:
        if ticket is not None:
            ticket.unlink(missing_ok=True)
        if ticket_descriptor is not None:
            os.close(ticket_descriptor)
        if queue is not None:
            os.close(queue)
        if permit is not None:
            os.close(permit)
