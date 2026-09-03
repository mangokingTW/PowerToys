"""Region selection at a different display scale.

The checklist's Mixed DPI lines ask to set the monitor to 100%, 150% and 200% and
verify the region selection and the OCR result at each.

The scale is changed live, with no sign-out. That correction is worth stating
plainly, because an earlier version of this work claimed a DPI change needs one:
the Settings UI does it live, and so does `DisplayConfigSetDeviceInfo` with the
(undocumented) `SET_SOURCE_DPI_SCALE` type. `DISPLAYCONFIG_SOURCE_DPI_SCALE_GET`
also reports which scales the display actually offers, relative to its
recommended one, which is what this test iterates over instead of asking for
150% and 200% and hoping.

The measured range is printed either way. On the local ARM64 VM at 800x600 it is
`min=0 cur=0 max=0` -- the display offers no alternative scale at all, which is
also why `HKCU\\Control Panel\\Desktop\\PerMonitorSettings` does not exist there.
On a host like that the test skips, and the skip carries the numbers so it cannot
be mistaken for a swallowed error.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import pytest
from wintegrate import Mouse

import powerocr_harness as h

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")

SOURCE_RECT = (80, 80, 700, 240)
SOURCE_TEXT = "Region selection at this scale"

#: The text goes on the *second* line, and the band covers only that line.
#:
#: After SetValue the caret sits at character position 0 -- immediately left of the
#: first glyph -- and the band's left edge is deliberately left of that glyph too, so
#: a caret drawn at that moment is inside the captured region. A vertical bar against
#: the left stroke of an R is a plausible H, which is exactly what x64 returned at the
#: larger scale while ARM64 read it correctly. The caret blinks, so whether it is in
#: the frame is a coin toss, and that fits an intermittent single-glyph error better
#: than the recogniser being worse at larger sizes.
#:
#: An empty first line keeps the caret above the band. That is deterministic in a way
#: that trying to take focus away from the editor is not.
SOURCE_LINES = f"\r\n{SOURCE_TEXT}"

QDC_ONLY_ACTIVE_PATHS = 0x00000002
GET_SOURCE_DPI_SCALE = -3
SET_SOURCE_DPI_SCALE = -4
MDT_EFFECTIVE_DPI = 0
MONITOR_DEFAULTTOPRIMARY = 1

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shcore = ctypes.WinDLL("shcore", use_last_error=True)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", _LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("statusFlags", wintypes.UINT),
    ]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class _TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", _LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("outputTechnology", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("scaling", wintypes.UINT),
        ("refreshRate", _RATIONAL),
        ("scanLineOrdering", wintypes.UINT),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.UINT),
    ]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", _SOURCE_INFO),
        ("targetInfo", _TARGET_INFO),
        ("flags", wintypes.UINT),
    ]


class _MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("infoType", wintypes.UINT),
        ("id", wintypes.UINT),
        ("adapterId", _LUID),
        ("blob", ctypes.c_byte * 64),
    ]


class _DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("size", wintypes.UINT),
        ("adapterId", _LUID),
        ("id", wintypes.UINT),
    ]


class _DPI_SCALE_GET(ctypes.Structure):
    _fields_ = [
        ("header", _DEVICE_INFO_HEADER),
        ("minScaleRel", ctypes.c_int),
        ("curScaleRel", ctypes.c_int),
        ("maxScaleRel", ctypes.c_int),
    ]


class _DPI_SCALE_SET(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER), ("scaleRel", ctypes.c_int)]


_user32.GetDisplayConfigBufferSizes.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(wintypes.UINT),
]
_user32.QueryDisplayConfig.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(_PATH_INFO),
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(_MODE_INFO),
    ctypes.c_void_p,
]
_user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(_DEVICE_INFO_HEADER)]
_user32.DisplayConfigSetDeviceInfo.argtypes = [ctypes.POINTER(_DEVICE_INFO_HEADER)]
_shcore.GetDpiForMonitor.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(wintypes.UINT),
]


def _primary_source():
    """The primary display's (adapterId, id), which the DPI calls are keyed on."""
    paths = wintypes.UINT()
    modes = wintypes.UINT()
    if (
        _user32.GetDisplayConfigBufferSizes(
            QDC_ONLY_ACTIVE_PATHS, ctypes.byref(paths), ctypes.byref(modes)
        )
        != 0
    ):
        return None
    path_array = (_PATH_INFO * paths.value)()
    mode_array = (_MODE_INFO * modes.value)()
    if (
        _user32.QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(paths),
            path_array,
            ctypes.byref(modes),
            mode_array,
            None,
        )
        != 0
    ):
        return None
    if not paths.value:
        return None
    return path_array[0].sourceInfo


def _scale_range(source) -> tuple[int, int, int] | None:
    """(min, current, max) scale indices *relative to the recommended scale*."""
    query = _DPI_SCALE_GET()
    query.header.type = GET_SOURCE_DPI_SCALE
    query.header.size = ctypes.sizeof(_DPI_SCALE_GET)
    query.header.adapterId = source.adapterId
    query.header.id = source.id
    if _user32.DisplayConfigGetDeviceInfo(ctypes.byref(query.header)) != 0:
        return None
    return (query.minScaleRel, query.curScaleRel, query.maxScaleRel)


def _scale_took(source, relative: int) -> bool:
    """Whether the display now reports `relative` as its current scale.

    Read back through the same DisplayConfig API that set it, deliberately.
    `GetDpiForMonitor` is not usable as the check: a DPI-unaware process is told 96
    whatever the display is really doing, and this one is unaware. Measured on the
    runners -- scale +0 and scale +1 both reported an effective DPI of 96, which
    said nothing about whether the change landed.
    """
    current = _scale_range(source)
    return current is not None and current[1] == relative


def _set_scale(source, relative: int) -> bool:
    request = _DPI_SCALE_SET()
    request.header.type = SET_SOURCE_DPI_SCALE
    request.header.size = ctypes.sizeof(_DPI_SCALE_SET)
    request.header.adapterId = source.adapterId
    request.header.id = source.id
    request.scaleRel = relative
    ok = _user32.DisplayConfigSetDeviceInfo(ctypes.byref(request.header)) == 0
    time.sleep(3)
    return ok


def _effective_dpi() -> int:
    monitor = _user32.MonitorFromWindow(_user32.GetDesktopWindow(), MONITOR_DEFAULTTOPRIMARY)
    x = wintypes.UINT()
    y = wintypes.UINT()
    _shcore.GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, ctypes.byref(x), ctypes.byref(y))
    return x.value


def _capture_the_source_line(executable) -> str | None:
    """Puts one known line on screen, drags a band over it, returns what came back."""
    from wintegrate.apps import sweep_processes_verified

    h.sweep()
    sweep_processes_verified(["notepad.exe", "Notepad.exe"])
    time.sleep(0.6)
    _window, editor, _first, second = h.source_with_text(SOURCE_RECT, SOURCE_LINES)

    powerocr = subprocess.Popen([str(executable), str(os.getpid())])
    try:
        time.sleep(2.0)
        assert h.signal_show_event(), f"could not open {h.SHOW_EVENT_NAME}"
        overlays = h.wait_for_overlay(timeout=15.0)
        assert overlays, "the overlay did not come up"
        print(f"  overlay {overlays[0][1]}")
        # `second` is the line the text is on; the caret stays on the empty line
        # above it, outside the band.
        #
        # The band's top gets half a line of headroom rather than the 4px the shared
        # helper uses. At the larger scale ARM64 returned
        # 'Region se ec Ion a\r\nIS sca e' -- and every character it lost, the l's
        # and t's, is one with an ascender. The glyphs grow with the scale and 4px
        # stopped clearing their tops.
        line_height = second[2] - second[1]
        top = second[1] - line_height // 2
        print(
            f"  editor {editor.bounding_rectangle} line2 {second} "
            f"line_height {line_height} band_top {top}"
        )
        return h.drag_band(
            Mouse(), editor, (second[0], top, second[2]), second[2], window_x=SOURCE_RECT[0]
        )
    finally:
        powerocr.terminate()
        h.sweep()
        sweep_processes_verified(["notepad.exe", "Notepad.exe"])


def test_region_selection_survives_a_display_scale_change(recording):
    """*Set monitors to 100% / 150% / 200% DPI; activate the overlay and verify
    correct region selection and OCR result.*

    Every scale the display offers is visited, not a hardcoded list of three: the
    display reports its own range, and asking for a scale it does not have would
    test the request rather than the product. The selection and the extracted text
    are asserted at each one, which is what the checklist line is about -- the
    coordinate conversion between WinUI DIPs and physical pixels is exactly what a
    scale change stresses.
    """
    executable = h.powerocr_executable()
    source = _primary_source()
    assert source is not None, "could not read the display configuration"

    scales = _scale_range(source)
    assert scales is not None, "the display does not answer GET_SOURCE_DPI_SCALE"
    low, current, high = scales
    print(f"scale range relative to recommended: min={low} cur={current} max={high}")
    print(f"effective DPI now: {_effective_dpi()}")

    if low == high:
        pytest.skip(
            f"this display offers a single scale (min={low}, cur={current}, max={high}, "
            f"effective DPI {_effective_dpi()}), so there is no second scale to select "
            f"a region at. Not a failure and not a swallowed error -- the API answered, "
            f"and the answer is that this host has nothing to change"
        )

    h.pin_ocr_language(executable)
    results = {}
    try:
        for relative in range(low, high + 1):
            assert _set_scale(source, relative), f"the display refused scale {relative}"
            assert _scale_took(source, relative), (
                f"the display still reports {_scale_range(source)} after being asked "
                f"for scale {relative:+d}, so the change did not land"
            )
            # Printed for context only; see _scale_took for why it is not the check.
            dpi = _effective_dpi()
            print(f"scale {relative:+d} accepted (process-visible DPI {dpi})")
            extracted = _capture_the_source_line(executable)
            assert extracted is not None, (
                f"nothing was published within 20s at scale {relative:+d} (effective DPI {dpi})"
            )
            results[relative] = (dpi, extracted)
            print(f"  scale {relative:+d} (DPI {dpi}): {extracted!a}")
    finally:
        _set_scale(source, current)
        print(f"scale restored to {current:+d}, effective DPI {_effective_dpi()}")

    expected = h.normalise(SOURCE_TEXT)
    for relative, (dpi, extracted) in sorted(results.items()):
        assert h.normalise(extracted) == expected, (
            f"at scale {relative:+d} (process-visible DPI {dpi}) the selection "
            f"returned {h.normalise(extracted)!a} instead of {expected!r}. A "
            f"shorter result means the band is not where it was asked to be; a "
            f"same-length one means something else was in the band, and the caret is "
            f"the thing most likely to be"
        )

    assert len(results) > 1, (
        f"only one scale was exercised ({sorted(results)}), so nothing here is about a "
        f"scale *change*"
    )
