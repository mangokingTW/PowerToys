"""Recording support for PowerOCR WinUI 3 verification.

Captures unattended Windows CI executions into MP4 recordings with native mouse & keyboard overlays.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from pathlib import Path

import pytest
from wintegrate import Window
from wintegrate.interop import SM_CXSCREEN, SM_CYSCREEN, user32

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


def pytest_runtest_logstart(nodeid, location):
    """Names the running test in the recording's bottom-left corner.

    Which test a stretch of a recording belongs to is otherwise a matter of
    counting windows and guessing, and the stretch worth watching is usually the
    one nobody can find. The recorder draws whatever is in `caption` into each
    frame, so setting it here is enough.

    The file goes on the second line and the test's own name on the first, because
    the name is what a viewer is looking for and a long path would push it out of
    the panel.
    """
    if _active_recorder is None:
        return
    recorder, _output = _active_recorder
    filename, _lineno, _domain = location
    recorder.caption = nodeid.split("::")[-1]
    recorder.caption_subtitle = str(filename)


def pytest_runtest_logfinish(nodeid, location):
    """Clears the caption between tests, so a frame never names the wrong one."""
    if _active_recorder is None:
        return
    recorder, _output = _active_recorder
    recorder.caption = ""
    recorder.caption_subtitle = ""


@pytest.fixture(scope="session")
def recording():
    """Fixture alias for compatibility with test signatures."""
    yield


@pytest.fixture
def overlay():
    """A raised Text Extractor overlay, as a Window.

    Function-scoped on purpose. One of these tests dismisses the overlay with
    Escape, and a shared fixture would leave the next test to guess whether it is
    still up -- which is how a suite reports success without having exercised
    anything. Starting the process per test costs a few seconds and removes the
    question.

    Failure to raise it fails the test rather than skipping it. "PowerOCR overlay
    not detected" was the skip reason an earlier version printed on every run while
    the real fault was a call to a function that does not exist.
    """
    import powerocr_harness as harness

    executable = harness.powerocr_executable()
    harness.sweep()
    was = harness.pin_ocr_language(executable)
    print(f"OCR language pinned to {harness.OCR_LANGUAGE!r} (was {was!r})")

    process = subprocess.Popen([str(executable), str(os.getpid())])
    try:
        time.sleep(2.0)
        already = harness.visible_overlay_windows()
        assert not already, (
            f"PowerOCR has a visible window before its show event was signalled, so "
            f"nothing here can be attributed to the activation: {already}"
        )
        assert harness.signal_show_event(), (
            f"could not open {harness.SHOW_EVENT_NAME}; PowerOCR is not waiting on its "
            f"show event, so the overlay was never raised"
        )
        overlays = harness.wait_for_overlay(timeout=15.0)
        assert overlays, (
            f"no visible window appeared within 15s of signalling {harness.SHOW_EVENT_NAME}"
        )

        screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
        screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
        primary = harness.overlay_covering_primary(overlays, screen_w, screen_h)
        assert primary, (
            f"no overlay covers the {screen_w}x{screen_h} primary display to within "
            f"{harness.OVERLAY_EDGE_TOLERANCE}px; visible windows were {overlays}"
        )
        hwnd, rect, topmost, title = primary
        assert topmost, f"the overlay covering the primary display is not topmost: {primary}"
        print(f"overlay up: hwnd={hwnd:#x} rect={rect} topmost={topmost} title={title!r}")

        yield Window(hwnd)
    finally:
        process.terminate()
        harness.sweep()
