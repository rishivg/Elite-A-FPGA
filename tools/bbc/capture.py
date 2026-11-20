"""Run capture processes and set their BBC input environment."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import wait
from pathlib import Path

from .host_budget import capture_slot


def run(
    command: list[str], cwd: Path, log: Path, env=None, timeout=600, pack=None, *, exit_codes=(0,)
):
    # Start timing after a host capture slot becomes available.
    streamed = {}
    with capture_slot() as permit, log.open("w") as output:
        names = []
        if pack is not None and Path(command[0]).name in {"VBbcMachine", "VBbcMachine96"}:
            if Path(command[-1]).resolve() != Path(pack).resolve():
                raise ValueError("Native audio stream must belong to the actual output directory")
            names = ["audio.u8"]
        elif (
            pack is not None
            and len(command) == 3
            and Path(command[0]).name == "node"
            and Path(command[1]).resolve() == Path(__file__).with_name("reference.mjs").resolve()
        ):
            configuration = json.loads(Path(command[2]).read_text())
            if Path(configuration["output"]).resolve() != Path(pack).resolve():
                raise ValueError(
                    "Reference audio streams must belong to the actual output directory"
                )
            if configuration.get("scenario"):
                names = ["audio-digital.u8", "audio-pins.u8"]
        streams = []
        try:
            if names:
                from .audio_stream import AudioStream

                for name in names:
                    streams.append(AudioStream(pack, name))
            result = subprocess.run(
                command,
                cwd=cwd,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
                pass_fds=(permit,),
            )
        except BaseException as error:
            for audio in streams:
                try:
                    audio.finish(False)
                except BaseException as cleanup_error:
                    error.add_note("Audio cleanup failed: " + str(cleanup_error))
            raise
        errors = []
        for audio in streams:
            try:
                info = audio.finish(result.returncode in exit_codes)
                if info is not None:
                    streamed[audio.fifo.name] = info
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]
        if result.returncode not in exit_codes:
            raise subprocess.CalledProcessError(result.returncode, command)
    if pack is not None:
        from .storage import pack as pack_capture

        if not streamed:
            pack_capture(pack)
        else:
            pack_capture(pack, pre_encoded=streamed)


def scenario_command(binary, directory, scenario, output):
    return [
        str(binary),
        str(scenario["duration_us"] * 32),
        str(directory / "keys.tsv"),
        str(directory / "checkpoints.tsv"),
        str(directory / output),
    ]


def capture_pair(executor, dut, reference):
    # Wait for both writers before surfacing either capture's failure.
    futures = [executor.submit(dut), executor.submit(reference)]
    wait(futures)
    for future in futures:
        future.result()


def native_environment(
    load=None,
    save=None,
    trace=False,
    analogue=None,
    disc=None,
    ram_trace=False,
    cold_prefix=False,
    restart=None,
):
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "BBC_LOAD_STATE",
            "BBC_SAVE_STATE",
            "BBC_BUS_TRACE",
            "BBC_RAM_TRACE",
            "BBC_CAPTURE_PREFIX",
            "BBC_ANALOGUE_EVENTS",
            "BBC_DISC_IMAGE",
            "BBC_RESTART_EVENTS",
        }
    }
    if load:
        env["BBC_LOAD_STATE"] = str(load)
    if save:
        env["BBC_SAVE_STATE"] = str(save)
    if trace:
        env["BBC_BUS_TRACE"] = "1"
    if ram_trace:
        env["BBC_RAM_TRACE"] = "1"
    if cold_prefix:
        if load or save:
            raise ValueError("A cold prefix cannot restore or save a game snapshot")
        env["BBC_CAPTURE_PREFIX"] = "1"
    if analogue:
        env["BBC_ANALOGUE_EVENTS"] = str(analogue)
    if restart:
        if load or save:
            raise ValueError("A restart capture must execute its own cold reset")
        env["BBC_RESTART_EVENTS"] = str(restart)
    if disc:
        if load:
            raise ValueError("A restore must use its own saved disc image")
        env["BBC_DISC_IMAGE"] = str(disc)
    return env
