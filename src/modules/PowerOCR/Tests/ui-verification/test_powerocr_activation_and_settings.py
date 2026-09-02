"""Runner-managed activation, and the Settings deep-link.

The checklist's other two "Activation paths" and "Settings integration" lines. Both
looked impossible at first and both turned out to be a matter of finding the right
thing to look at:

- `Win+Shift+T` did nothing, and so did signalling the named event, with the Runner
  running. The Runner's own settings said why: `enabled.TextExtractor` was `false`.
  A disabled module has nothing listening, and the hotkey is registered by the
  Runner rather than by the module. The test enables it and restarts the Runner.
- the Settings deep-link opens a localized page, so there is nothing stable to
  match on in its text. It does carry AutomationIds --
  `EnableTextExtractorToggleSwitch` among them -- and only the Text Extractor page
  has those.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from wintegrate import (
    UiaElement,
    Window,
    WindowCensus,
    get_process_image_name,
    send_hotkey,
)
from wintegrate.apps import sweep_processes_verified

import powerocr_harness as h

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")

RUNNER = Path(os.environ.get("LOCALAPPDATA", "")) / "PowerToys" / "PowerToys.exe"
SETTINGS_PROCESS = "PowerToys.Settings.exe"

#: The Runner keeps one file for which modules are on.
GENERAL_SETTINGS = (
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    / "Microsoft"
    / "PowerToys"
    / "settings.json"
)

#: Its key for this module is the display name, as with the module's own folder.
MODULE_KEY = "TextExtractor"

#: Win+Shift+T, which is `DefaultActivationShortcut` in PowerOcrProperties
#: (win: true, shift: true, code 0x54).
ACTIVATION_HOTKEY = "win+shift+t"

#: An AutomationId that exists only on the Text Extractor settings page. Used
#: instead of matching its heading, which is localized -- the same page reads
#: "Text Extractor" on an en-US runner and something else everywhere else.
SETTINGS_PAGE_ANCHOR = "EnableTextExtractorToggleSwitch"


def _stop_powertoys() -> None:
    sweep_processes_verified(["PowerToys.exe", SETTINGS_PROCESS])
    h.sweep()


def _settings_windows() -> list[tuple[int, str]]:
    found = []
    for snap in WindowCensus.capture():
        if not snap.is_visible:
            continue
        image = (get_process_image_name(snap.pid) or "").lower()
        if image.endswith(SETTINGS_PROCESS.lower()):
            found.append((snap.hwnd, snap.title))
    return found


def _enable_module() -> bool | None:
    """Turns the module on in the Runner's settings, returning what it was.

    The file is created by the Runner, not by this test: if it is missing the Runner
    is started once to write it. Only the one key is touched.
    """
    if not GENERAL_SETTINGS.exists():
        seeder = subprocess.Popen([str(RUNNER)])
        try:
            deadline = time.monotonic() + 40.0
            while time.monotonic() < deadline and not GENERAL_SETTINGS.exists():
                time.sleep(0.5)
        finally:
            seeder.terminate()
        _stop_powertoys()
    if not GENERAL_SETTINGS.exists():
        return None

    data = json.loads(GENERAL_SETTINGS.read_text(encoding="utf-8-sig"))
    enabled = data.setdefault("enabled", {})
    previous = enabled.get(MODULE_KEY)
    enabled[MODULE_KEY] = True
    GENERAL_SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return previous


def test_the_runner_hotkey_raises_the_overlay():
    """*Start PowerToys Runner normally; activate Text Extractor via `Win+Shift+T`
    and verify the overlay appears (Runner-managed activation).*

    This is the path a person uses, and it is the one that needs the Runner: the
    hotkey is registered by the Runner's low-level keyboard hook, not by the module.

    Measured before it worked: with `enabled.TextExtractor` false, neither the
    hotkey nor signalling the module's named event raised anything, and the event
    could still be *opened*, so "the event exists" is not a readiness signal.
    """
    assert RUNNER.exists(), f"the Runner is not at {RUNNER}"
    _stop_powertoys()

    was = _enable_module()
    print(f"enabled.{MODULE_KEY}: {was!r} -> True")
    assert was is not None, (
        f"{GENERAL_SETTINGS} does not exist even after starting the Runner, so there "
        f"is no way to tell whether the module is on"
    )

    runner = subprocess.Popen([str(RUNNER)])
    try:
        # The Runner needs to come up, read its settings and register the hotkey.
        time.sleep(15)
        # It opens Settings on a first run; that would take the foreground.
        sweep_processes_verified([SETTINGS_PROCESS])
        time.sleep(2)

        before = h.visible_overlay_windows()
        assert not before, (
            f"an overlay is already up before the hotkey was sent, so nothing here "
            f"can be attributed to it: {before}"
        )

        assert send_hotkey(ACTIVATION_HOTKEY), f"the system refused to inject {ACTIVATION_HOTKEY}"
        overlays = h.wait_for_overlay(timeout=20.0)
        assert overlays, (
            f"{ACTIVATION_HOTKEY} did not raise the overlay within 20s. The module "
            f"reports enabled={MODULE_KEY!r} in {GENERAL_SETTINGS}, and the Runner "
            f"had 15s to register the hotkey"
        )
        hwnd, rect, topmost, title = overlays[0]
        print(f"hotkey raised: hwnd={hwnd:#x} rect={rect} topmost={topmost} title={title!r}")
        assert topmost, f"the overlay the hotkey raised is not topmost: {overlays[0]}"
    finally:
        runner.terminate()
        _stop_powertoys()


def test_the_overlay_settings_button_opens_the_text_extractor_page():
    """*In PowerToys Settings, click the deep-link (`SettingsButton`) inside the
    Text Extractor overlay; verify that the Settings page scrolls to or opens the
    Text Extractor section.*

    Asserted on an AutomationId rather than on the page's heading. The heading is
    localized, so matching it would be asserting the runner's display language;
    `EnableTextExtractorToggleSwitch` is on that page and on no other.
    """
    executable = h.powerocr_executable()
    _stop_powertoys()
    h.pin_ocr_language(executable)

    powerocr = subprocess.Popen([str(executable), str(os.getpid())])
    try:
        time.sleep(2.0)
        assert not _settings_windows(), "PowerToys Settings is already open"
        assert h.signal_show_event(), f"could not open {h.SHOW_EVENT_NAME}"
        overlays = h.wait_for_overlay(timeout=15.0)
        assert overlays, "the overlay did not come up"

        overlay = Window(overlays[0][0])
        button = UiaElement.from_handle(overlay.hwnd).find_all(automation_id=h.SETTINGS_ID)
        assert button, f"the overlay has no {h.SETTINGS_ID}"
        button[0].invoke()

        # Settings is a WinUI 3 app and cold-starts slowly on a runner.
        deadline = time.monotonic() + 60.0
        windows: list[tuple[int, str]] = []
        while time.monotonic() < deadline:
            windows = _settings_windows()
            if windows:
                break
            time.sleep(0.5)
        assert windows, "PowerToys Settings did not open within 60s of the deep-link"
        hwnd, title = windows[0]
        print(f"settings window: hwnd={hwnd:#x} title={title!a}")

        # The page itself takes a moment to render after the window appears.
        deadline = time.monotonic() + 30.0
        anchor = []
        while time.monotonic() < deadline:
            anchor = UiaElement.from_handle(hwnd).find_all(automation_id=SETTINGS_PAGE_ANCHOR)
            if anchor:
                break
            time.sleep(0.5)
        assert anchor, (
            f"PowerToys Settings opened but {SETTINGS_PAGE_ANCHOR!r} is not in it "
            f"within 30s, so it is not showing the Text Extractor page"
        )
        print(f"found {SETTINGS_PAGE_ANCHOR}: name={anchor[0].name!a}")
    finally:
        powerocr.terminate()
        _stop_powertoys()
