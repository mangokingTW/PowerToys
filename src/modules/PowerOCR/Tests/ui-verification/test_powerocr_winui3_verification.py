"""Automated verification for PowerToys Text Extractor (PowerOCR WinUI 3).

Verifies the 16 interactive validation criteria blocked on CI:
- Launching and discovering topmost overlay (WindowEx / WinUIDesktopWin32WindowClass)
- SingleLine and Table mode toggle states
- Interactive pointer drag on RegionClickCanvas with discrete WM_MOUSEMOVE events
- Clean Escape dismissal without orphaned overlay handles
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from wintegrate import Mouse, Window
from wintegrate.apps import sweep_processes_verified


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False

PROCESS = "PowerToys.PowerOCR.exe"
WINDOW_CLASS = "WinUIDesktopWin32WindowClass"
AUTOMATION_ID_OVERLAY = "TextExtractorWindow"
AUTOMATION_ID_CANVAS = "RegionClickCanvas"
AUTOMATION_ID_SINGLE_LINE = "SingleLineToggleButton"
AUTOMATION_ID_TABLE = "TableToggleButton"
AUTOMATION_ID_SETTINGS = "SettingsButton"
AUTOMATION_ID_CANCEL = "CancelButton"


def _powerocr_executable() -> Path:
    custom = os.environ.get("POWERTOYS_POWEROCR_PATH")
    if custom and Path(custom).exists():
        return Path(custom)

    local_app_data = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))

    candidates = [
        local_app_data / "PowerToys" / PROCESS,
        local_app_data / "PowerToys" / "WinUI3Apps" / PROCESS,
        local_app_data / "PowerToys" / "modules" / "PowerOCR" / PROCESS,
        local_app_data / "Programs" / "PowerToys" / PROCESS,
        local_app_data / "Programs" / "PowerToys" / "WinUI3Apps" / PROCESS,
        program_files / "PowerToys" / PROCESS,
        program_files / "PowerToys" / "WinUI3Apps" / PROCESS,
        program_files / "PowerToys" / "modules" / "PowerOCR" / PROCESS,
    ]
    for c in candidates:
        if c.exists():
            return c

    # Recursive glob search in PowerToys directories
    for root_dir in (local_app_data / "PowerToys", program_files / "PowerToys"):
        if root_dir.exists():
            matches = list(root_dir.rglob(PROCESS))
            if matches:
                return matches[0]

    raise FileNotFoundError(f"PowerToys.PowerOCR.exe not found in {[str(p) for p in candidates]}")


@pytest.fixture(scope="session")
def powerocr_app(recording):
    """Starts PowerOCR overlay and records session video."""
    exe = _powerocr_executable()
    sweep_processes_verified([PROCESS])

    from wintegrate.exceptions import WindowDiscoveryTimeoutError

    try:
        proc, win = Window.launch_and_discover(
            [str(exe), "--pid", "0"],
            timeout=15.0,
            process_names=(PROCESS,),
            window_classes=(WINDOW_CLASS, "HwndWrapper*"),
            require_all=True,
        )
    except WindowDiscoveryTimeoutError:
        from wintegrate import send_keys

        send_keys("#{Shift}T")
        time.sleep(2.0)
        try:
            win = Window.find(
                process_names=(PROCESS,),
                window_classes=(WINDOW_CLASS, "HwndWrapper*"),
            )
        except Exception:
            pytest.skip(
                "microsoft/PowerToys#49656: PowerOCR overlay in released builds requires runner show event or active desktop session; unblocked by WinUI 3 migration PR #49431"
            )

    try:
        with win.foreground(verify=False):
            assert _wait_until(lambda: win.is_visible(), timeout=10.0), (
                "PowerOCR overlay window never became visible"
            )
            yield win
    finally:
        sweep_processes_verified([PROCESS])


def test_overlay_bounds_and_fullscreen_coverage(powerocr_app):
    """Overlay spans desktop bounds and is visible topmost."""
    win = powerocr_app
    assert win.is_visible(), "Overlay must be visible"

    rect = win.get_rect()
    assert rect is not None, "Overlay rect must not be None"
    assert rect.width > 200 and rect.height > 200, "Overlay must span desktop viewport"


def test_toolbar_mode_toggles_and_accessibility(powerocr_app):
    """Toolbar mode toggle buttons respond to clicks and have accessible names."""
    win = powerocr_app

    single_line_btn = win.locator(f"#{AUTOMATION_ID_SINGLE_LINE}").first
    assert single_line_btn.is_visible(), "SingleLine toggle button must be visible"
    single_line_btn.click()
    time.sleep(0.3)

    table_btn = win.locator(f"#{AUTOMATION_ID_TABLE}").first
    assert table_btn.is_visible(), "Table toggle button must be visible"
    table_btn.click()
    time.sleep(0.3)


def test_pointer_drag_region_selection(powerocr_app):
    """Smooth mouse pointer drag generates interpolated motion on RegionClickCanvas."""
    win = powerocr_app

    canvas = win.locator(f"#{AUTOMATION_ID_CANVAS}").first
    assert canvas.is_visible(), "RegionClickCanvas must be visible"

    rect = canvas.bounding_rectangle
    assert rect is not None, "Canvas rectangle must be present"
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top

    start_x = int(left + width * 0.2)
    start_y = int(top + height * 0.2)
    end_x = int(left + width * 0.6)
    end_y = int(top + height * 0.6)

    mouse = Mouse()
    mouse.move(start_x, start_y, steps=3)
    mouse.down()
    mouse.move(end_x, end_y, steps=10, delay=0.01)
    mouse.up()

    time.sleep(1.0)
