"""The overlay under the light and dark system themes.

The checklist asks to *"switch Windows to Light theme; activate the overlay and
verify the toolbar and canvas render correctly"*, and the same for Dark. "Renders
correctly" is a human judgement and this does not pretend otherwise. What it does
assert is the part that can be measured, and that is more than it sounds:

- the theme actually reached the overlay, by sampling the toolbar's pixels under
  both themes and requiring them to differ in the expected direction. Measured:
  the Settings button averages RGB (230, 230, 230) under Light and (52, 52, 52)
  under Dark.
- every toolbar control is still present, enabled and has a rectangle under both.
  A control that renders in one theme and vanishes in the other is exactly the
  kind of regression this line exists to catch, and that part is checkable.

Written as one test over both themes rather than two, so the comparison is
self-calibrating: no threshold has to be guessed for "bright enough".
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import winreg
from ctypes import wintypes

import pytest
from wintegrate import UiaElement, Window, capture_screen_image

import powerocr_harness as h

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")

PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

#: Light minus dark, summed over the three channels, has to clear this. Measured at
#: 534 on the ARM64 VM (230,230,230 against 52,52,52), so the bar is a long way
#: below what a working theme switch produces and a long way above sampling noise.
MIN_THEME_DELTA = 120

#: Each toolbar button's luminance range has to clear this for its glyph to be
#: drawn against its background at all. A legible icon has both dark and light
#: pixels; a control rendered as a flat block has neither. Measured per button:
#: 188-194 under the normal theme and 220-223 under High Contrast Black, so this
#: is well under both and far above the ~0 a flat block would give.
MIN_GLYPH_SPREAD = 120

_SPI_GETHIGHCONTRAST = 0x0042
_SPI_SETHIGHCONTRAST = 0x0043
_HCF_HIGHCONTRASTON = 0x0001
_SPIF_SENDCHANGE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPCWSTR,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(wintypes.DWORD),
]
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


class _HIGHCONTRASTW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwFlags", wintypes.DWORD),
        ("lpszDefaultScheme", wintypes.LPWSTR),
    ]


def _high_contrast_flags() -> int:
    state = _HIGHCONTRASTW()
    state.cbSize = ctypes.sizeof(_HIGHCONTRASTW)
    _user32.SystemParametersInfoW(_SPI_GETHIGHCONTRAST, state.cbSize, ctypes.byref(state), 0)
    return int(state.dwFlags)


def _set_high_contrast(on: bool, scheme: str = "High Contrast Black") -> None:
    """Turns High Contrast on or off, and tells applications about it."""
    state = _HIGHCONTRASTW()
    state.cbSize = ctypes.sizeof(_HIGHCONTRASTW)
    base = _high_contrast_flags()
    state.dwFlags = (base | _HCF_HIGHCONTRASTON) if on else (base & ~_HCF_HIGHCONTRASTON)
    state.lpszDefaultScheme = ctypes.c_wchar_p(scheme) if on else None
    _user32.SystemParametersInfoW(
        _SPI_SETHIGHCONTRAST, state.cbSize, ctypes.byref(state), _SPIF_SENDCHANGE
    )
    time.sleep(4)


def _read_apps_use_light_theme() -> int | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as key:
            return int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return None


def _set_theme(light: bool) -> None:
    """Switches the system theme and tells running applications about it.

    The registry value alone changes nothing that is already on screen; the
    broadcast is what makes applications re-read it. Both topics are sent because
    different frameworks listen for different ones.
    """
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, PERSONALIZE, 0, winreg.KEY_SET_VALUE) as key:
        value = 1 if light else 0
        winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)

    result = wintypes.DWORD()
    for topic in ("ImmersiveColorSet", "WindowsThemeElement"):
        _user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            topic,
            SMTO_ABORTIFHUNG,
            3000,
            ctypes.byref(result),
        )
    time.sleep(2.5)


def _mean_rgb(rect: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """The average colour of a screen rectangle."""
    left, top, right, bottom = rect
    image = capture_screen_image().convert("RGB").crop((left, top, right, bottom))
    pixels = list(image.getdata())
    assert pixels, f"the rectangle {rect} contains no pixels"
    return tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))


def _sample_toolbar_under_current_theme(executable) -> tuple[tuple[int, int, int], dict]:
    """Raises the overlay and reports the toolbar's colour and each control's state."""
    h.sweep()
    time.sleep(0.5)
    powerocr = subprocess.Popen([str(executable), str(os.getpid())])
    try:
        time.sleep(2.2)
        assert h.signal_show_event(), f"could not open {h.SHOW_EVENT_NAME}"
        overlays = h.wait_for_overlay(timeout=15.0)
        assert overlays, "the overlay did not come up"

        root = UiaElement.from_handle(Window(overlays[0][0]).hwnd)
        states = {}
        for automation_id in h.TOOLBAR_BUTTON_IDS:
            found = root.find_all(automation_id=automation_id)
            assert found, f"{automation_id} is not in the overlay under this theme"
            element = found[0]
            rect = element.bounding_rectangle
            assert rect and rect[2] > rect[0] and rect[3] > rect[1], (
                f"{automation_id} has no rectangle under this theme: {rect}"
            )
            states[automation_id] = {
                "enabled": bool(element.is_enabled()),
                "rect": rect,
                "name": element.name or "",
            }

        settings_rect = states[h.SETTINGS_ID]["rect"]
        return _mean_rgb(settings_rect), states
    finally:
        powerocr.terminate()
        h.sweep()


def test_the_overlay_follows_the_light_and_dark_themes(recording):
    """*Switch Windows to Light / Dark theme; activate the overlay and verify the
    toolbar and canvas render correctly.*

    The rendering itself is not something a test can judge. That the theme reached
    the overlay, and that nothing disappeared from the toolbar in either, is.
    """
    executable = h.powerocr_executable()
    original = _read_apps_use_light_theme()
    assert original is not None, (
        f"HKCU\\{PERSONALIZE}\\AppsUseLightTheme does not exist, so the theme cannot "
        f"be set or restored on this host"
    )
    print(f"AppsUseLightTheme was {original}")
    h.pin_ocr_language(executable)

    samples = {}
    try:
        for light in (True, False):
            _set_theme(light)
            applied = _read_apps_use_light_theme()
            assert applied == (1 if light else 0), (
                f"the theme did not take: asked for "
                f"{'light' if light else 'dark'}, registry says {applied}"
            )
            colour, states = _sample_toolbar_under_current_theme(executable)
            samples[light] = colour
            print(
                f"{'light' if light else 'dark':>5}: toolbar mean RGB {colour}, "
                f"controls "
                + ", ".join(f"{k}={'on' if v['enabled'] else 'OFF'}" for k, v in states.items())
            )
            for automation_id, state in states.items():
                assert state["enabled"], (
                    f"{automation_id} is disabled under the {'light' if light else 'dark'} theme"
                )
                assert state["name"].strip(), (
                    f"{automation_id} lost its accessible name under the "
                    f"{'light' if light else 'dark'} theme"
                )
    finally:
        _set_theme(original == 1)
        print(f"AppsUseLightTheme restored to {_read_apps_use_light_theme()}")

    light_rgb, dark_rgb = samples[True], samples[False]
    delta = sum(light_rgb[i] - dark_rgb[i] for i in range(3))
    print(f"light {light_rgb} - dark {dark_rgb} = {delta}")
    assert delta >= MIN_THEME_DELTA, (
        f"the toolbar looks the same under both themes (light {light_rgb}, dark "
        f"{dark_rgb}, difference {delta} < {MIN_THEME_DELTA}), so either the theme "
        f"switch did not reach the overlay or the overlay ignores it"
    )


def _glyph_spreads(executable) -> dict[str, tuple[int, int]]:
    """Each toolbar button's luminance range, with the overlay raised."""
    h.sweep()
    time.sleep(0.5)
    powerocr = subprocess.Popen([str(executable), str(os.getpid())])
    try:
        time.sleep(2.2)
        assert h.signal_show_event(), f"could not open {h.SHOW_EVENT_NAME}"
        overlays = h.wait_for_overlay(timeout=15.0)
        assert overlays, "the overlay did not come up"

        root = UiaElement.from_handle(Window(overlays[0][0]).hwnd)
        grey = capture_screen_image().convert("L")
        out = {}
        for automation_id in h.TOOLBAR_BUTTON_IDS:
            found = root.find_all(automation_id=automation_id)
            assert found, f"{automation_id} is not in the overlay"
            element = found[0]
            assert element.is_enabled(), f"{automation_id} is disabled"
            assert (element.name or "").strip(), f"{automation_id} has no accessible name"
            left, top, right, bottom = element.bounding_rectangle
            out[automation_id] = grey.crop((left, top, right, bottom)).getextrema()
        return out
    finally:
        powerocr.terminate()
        h.sweep()


def test_the_toolbar_stays_legible_under_high_contrast(recording):
    """*Switch Windows to High Contrast (Black or White); activate the overlay and
    verify all controls are legible and accessible.*

    Legibility is not something a test can judge, so this asserts the measurable
    necessary condition: each button's rectangle has to contain both dark and light
    pixels, because a glyph drawn against a background does and a flat block does
    not. Accessibility is asserted directly -- present, enabled, named.

    Measured per button, which is where the threshold comes from:

        normal theme          49-243, spread 188-194
        High Contrast Black   32-255, spread 220-223

    Contrast goes up rather than down, which is the point of the scheme, and
    nothing disappeared.
    """
    executable = h.powerocr_executable()
    h.pin_ocr_language(executable)
    original = _high_contrast_flags()
    print(f"high-contrast flags were 0x{original:x} (on={bool(original & _HCF_HIGHCONTRASTON)})")

    try:
        _set_high_contrast(False)
        normal = _glyph_spreads(executable)
        print("normal:", {k: f"{lo}-{hi}" for k, (lo, hi) in normal.items()})

        _set_high_contrast(True)
        flags = _high_contrast_flags()
        assert flags & _HCF_HIGHCONTRASTON, (
            f"High Contrast did not turn on: flags 0x{flags:x}. Nothing measured after "
            f"this would be about High Contrast at all"
        )
        contrast = _glyph_spreads(executable)
        print("high contrast:", {k: f"{lo}-{hi}" for k, (lo, hi) in contrast.items()})
    finally:
        _set_high_contrast(bool(original & _HCF_HIGHCONTRASTON))
        print(f"high-contrast flags restored to 0x{_high_contrast_flags():x}")

    for automation_id, (low, high) in contrast.items():
        spread = high - low
        assert spread >= MIN_GLYPH_SPREAD, (
            f"{automation_id} spans only {low}-{high} (spread {spread}) under High "
            f"Contrast, which is what a control rendered without a visible glyph "
            f"looks like. Under the normal theme it was {normal[automation_id]}"
        )
