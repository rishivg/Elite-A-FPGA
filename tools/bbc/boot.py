"""Boot the two machines independently and cache their states."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import nullcontext

from tools.validation.common import ROOT, digest, source_root, write_json

from . import boot_cache
from .build import BBC, native_binary, native_cwd
from .capture import native_environment, run


def prepare(
    directory,
    reference_options,
    *,
    context=None,
    fresh=False,
    development=False,
    jobs=2,
    executor=None,
    optimized96=False,
):
    # The two backends have different working directories.
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    backends = boot_cache.backends(context)
    if optimized96:
        from . import profile96

        if boot_cache.clock_multiplier(context) != 3 or not profile96.current():
            raise ValueError("A verified current 96 MHz profile is required")
        binary = profile96.BINARY
    else:
        binary = native_binary(boot_cache.clock_multiplier(context))
    cached = {
        name: not fresh and not development and boot_cache.valid(name, context) for name in backends
    }

    def execute(backend):
        state = backends[backend][0]
        if backend == "dut":
            run(
                [
                    str(binary),
                    str(boot_cache.BOOT_CYCLES),
                    str(BBC / "boot.keys"),
                    "-",
                    str(directory / "boot-dut"),
                ],
                native_cwd(boot_cache.clock_multiplier(context)),
                directory / "boot-dut.log",
                native_environment(save=state, disc=boot_cache.disc_path(context)),
                timeout=boot_cache.BOOT_TIMEOUT_SECONDS * boot_cache.clock_multiplier(context),
            )
        else:
            config = {
                **reference_options,
                "jsbeeb": str(source_root("jsbeeb")),
                "disc": str(boot_cache.disc_path(context)),
                "keys": str(BBC / "boot.keys"),
                "cycles": boot_cache.BOOT_CYCLES,
                "save_state": str(state),
                "output": str(directory / "boot-reference"),
            }
            write_json(directory / "boot-reference.json", config)
            run(
                [
                    "node",
                    str(ROOT / "tools/bbc/reference.mjs"),
                    str(directory / "boot-reference.json"),
                ],
                ROOT,
                directory / "boot-reference.log",
                timeout=boot_cache.BOOT_TIMEOUT_SECONDS,
            )

    pending = {}
    for backend in backends:
        if cached[backend]:
            print("Reusing verified independently executed " + backend + " boot", flush=True)
        elif not development:
            pending[backend] = boot_cache.inputs(backend, context)
            print("Booting released Elite-A independently on " + backend, flush=True)
    # All media share the same pool of backend processes.
    pool = (
        ThreadPoolExecutor(max_workers=min(jobs, 2)) if executor is None else nullcontext(executor)
    )
    with pool as executor:
        futures = {name: executor.submit(execute, name) for name in pending}
        wait(futures.values())
        # Write each receipt after its backend finishes.
        for backend, future in futures.items():
            future.result()
            if optimized96 and not profile96.current():
                raise ValueError("96 MHz profile changed during independent boot")
            boot_cache.seal(directory, pending[backend], backend, context)
    paths = [p for states in backends.values() for p in states]
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Missing own-machine boot snapshot: {path}")
    return {
        "disc": str(boot_cache.disc_path(context)),
        "independently_booted_this_run": {
            name: not reused and not development for name, reused in cached.items()
        },
        "verified_cache_reused": cached,
        "independent_boot_receipt": json.loads(boot_cache.receipt_path(context).read_text())
        if not development
        else None,
        "hashes": {str(p): digest(p) for p in paths},
    }
