"""Recording support for the ZoomIt mirror-hotkey reproduction.

One MP4 per run, with the running test's name drawn into the bottom-left of
every frame -- otherwise the stretch worth watching is the one nobody can find.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

RECORDING_FPS = 10
OUTPUT_DIR = Path("recording-artifacts")

_active_recorder = None


def pytest_sessionstart(session):
    global _active_recorder
    if os.environ.get("WINTEGRATE_RECORD") != "1" or os.name != "nt":
        return
    try:
        from wintegrate import ContinuousRecorder

        arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = OUTPUT_DIR / f"repro-zoomit-mirror-hotkey-{arch}.mp4"
        recorder = ContinuousRecorder(output, fps=RECORDING_FPS)
        if recorder.start():
            _active_recorder = (recorder, output)
            print(f"session recording -> {output}")
    except Exception as exc:
        print(f"recording failed to start ({type(exc).__name__}: {exc})")


def pytest_sessionfinish(session, exitstatus):
    global _active_recorder
    if _active_recorder is None:
        return
    recorder, output = _active_recorder
    try:
        recorder.stop()
        size = output.stat().st_size if output.exists() else 0
        print(f"recording saved: {output} ({size / 1024:.0f} KB)")
    except Exception as exc:
        print(f"recording failed to stop cleanly ({type(exc).__name__}: {exc})")
    finally:
        _active_recorder = None


def pytest_runtest_logstart(nodeid, location):
    if _active_recorder is None:
        return
    recorder, _output = _active_recorder
    filename, _lineno, _domain = location
    recorder.caption = nodeid.split("::")[-1]
    recorder.caption_subtitle = str(filename)


def pytest_runtest_logfinish(nodeid, location):
    if _active_recorder is None:
        return
    recorder, _output = _active_recorder
    recorder.caption = ""
    recorder.caption_subtitle = ""
