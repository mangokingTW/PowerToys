"""Recording support for PowerOCR WinUI 3 verification.

Captures unattended Windows CI executions into MP4 recordings with native mouse & keyboard overlays.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

RECORDING_FPS = 10
OUTPUT_DIR = Path("recording-artifacts")

_active_recorder = None


def pytest_sessionstart(session):
    """Starts screen recording immediately when pytest initializes."""
    global _active_recorder
    if os.environ.get("WINTEGRATE_RECORD") == "1" and os.name == "nt":
        try:
            from wintegrate import ContinuousRecorder

            arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output = OUTPUT_DIR / f"verification-powerocr-winui3-{arch}.mp4"
            recorder = ContinuousRecorder(output, fps=RECORDING_FPS)
            if recorder.start():
                _active_recorder = (recorder, output)
                print(f"session recording -> {output}")
        except Exception as exc:
            print(f"recording failed to start ({type(exc).__name__}: {exc})")


def pytest_sessionfinish(session, exitstatus):
    """Stops screen recording and flushes video artifact when pytest exits."""
    global _active_recorder
    if _active_recorder is not None:
        recorder, output = _active_recorder
        try:
            recorder.stop()
            size = output.stat().st_size if output.exists() else 0
            print(f"recording saved: {output} ({size / 1024:.0f} KB)")
        except Exception as exc:
            print(f"recording failed to stop cleanly ({type(exc).__name__}: {exc})")
        finally:
            _active_recorder = None


@pytest.fixture(scope="session")
def recording():
    """Fixture alias for compatibility with test signatures."""
    yield
