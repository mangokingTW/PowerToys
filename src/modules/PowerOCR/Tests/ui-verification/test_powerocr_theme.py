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
