"""Shared machinery for the Text Extractor verification tests.

Kept out of the test files so both of them raise the overlay, pin the recogniser and
read the clipboard the same way. Everything here is about *getting to* the state a
checklist item describes; the assertions belong in the tests.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import time
from pathlib import Path

from wintegrate import WindowCensus, get_process_image_name
from wintegrate.interop import RECT, user32

PROCESS = "PowerToys.PowerOCR.exe"

#: PowerOCR waits on this event; setting it is what the PowerToys runner does when the
#: Text Extractor hotkey fires. Signalling it directly means the tests do not depend on
#: a hotkey reaching a detached desktop, which is one of the blockers.
SHOW_EVENT_NAME = r"Local\PowerOCREvent-dc864e06-e1af-4ecc-9078-f98bee745e3a"

#: The AutomationIds the module's own checklist names. Present and unchanged on the
#: shipped WPF build, which the PR says it preserves.
OVERLAY_ID = "TextExtractorWindow"
LANGUAGE_COMBO_ID = "OCROverlayLanguagesComboBox"
SINGLE_LINE_ID = "SingleLineToggleButton"
TABLE_ID = "TableToggleButton"
SETTINGS_ID = "SettingsButton"
CANCEL_ID = "CancelButton"

#: The four the checklist asks about by name, in the order they appear in the toolbar.
TOOLBAR_BUTTON_IDS = (SINGLE_LINE_ID, TABLE_ID, SETTINGS_ID, CANCEL_ID)

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

#: How far the overlay may fall short of a display edge and still count as covering it.
#: The left edge is not deterministic -- see `overlay_covering_primary`.
OVERLAY_EDGE_TOLERANCE = 2

#: PowerOCR's settings live under the module's *display* name, not its project name.
SETTINGS_PATH = (
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    / "Microsoft"
    / "PowerToys"
    / "TextExtractor"
    / "settings.json"
)

#: Matched against `Windows.Globalization.Language.NativeName`, which is what
#: OCROverlay compares `PreferredLanguage` to -- not a BCP-47 tag.
OCR_LANGUAGE = "English (United States)"


def powerocr_executable() -> Path:
    """Locates PowerOCR across the installer's layouts."""
    custom = os.environ.get("POWERTOYS_POWEROCR_PATH")
    if custom and Path(custom).exists():
        return Path(custom)

    local = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    roots = [
        local / "PowerToys",
        local / "Programs" / "PowerToys",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerToys",
    ]
    for root in roots:
        for sub in ("", "WinUI3Apps", r"modules\PowerOCR"):
            candidate = root / sub / PROCESS if sub else root / PROCESS
            if candidate.exists():
                return candidate
    for root in roots:
        if root.exists():
            found = list(root.rglob(PROCESS))
            if found:
                return found[0]

    raise FileNotFoundError(f"{PROCESS} is not under any of {[str(r) for r in roots]}")


def visible_overlay_windows() -> list[tuple[int, tuple[int, int, int, int], bool, str]]:
    """Every visible top-level window owned by PowerOCR, as (hwnd, rect, topmost, title).

    Enumerated through WindowCensus. `Window.find_all` does not exist, and calling it
    inside a bare `except Exception` is how an earlier version of these tests reported
    "overlay not detected" on every run while the real error was an AttributeError.

    Matched on ownership and geometry rather than on window class. The class carries a
    per-run GUID -- `HwndWrapper[PowerToys.PowerOCR;;<guid>]` on the shipped WPF build,
    `WinUIDesktopWin32WindowClass` after the migration -- so a class filter would be a
    filter on which build is installed.
    """
    found = []
    for snap in WindowCensus.capture():
        if not snap.is_visible:
            continue
        image = (get_process_image_name(snap.pid) or "").lower()
        if not image.endswith(PROCESS.lower()):
            continue
        rect = RECT()
        user32.GetWindowRect(snap.hwnd, ctypes.byref(rect))
        topmost = bool(user32.GetWindowLongW(snap.hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)
        found.append(
            (snap.hwnd, (rect.left, rect.top, rect.right, rect.bottom), topmost, snap.title)
        )
    return found


def overlay_covering_primary(overlays, screen_w: int, screen_h: int):
    """The overlay that covers the primary display, or None.

    Within `OVERLAY_EDGE_TOLERANCE`, deliberately: the left edge is not always 0. Eight
    samples on a 1024x768 runner --

        0.101.2362.0 (release)     x64 0, arm64 0, x64 0, arm64 1
        0.0.1.0 (build from main)  x64 1, arm64 1, x64 1, arm64 1

    -- so the value moves between runs of the same installer. The rect is printed by the
    callers, so a real change stays visible; asserting the exact edge would fail runs for
    a reason unrelated to what is being tested.
    """
    inset = OVERLAY_EDGE_TOLERANCE
    for entry in overlays:
        left, top, right, bottom = entry[1]
        if (
            left <= inset
            and top <= inset
            and right >= screen_w - inset
            and bottom >= screen_h - inset
        ):
            return entry
    return None


def pin_ocr_language(executable: Path) -> str | None:
    """Pins the recogniser to English, and reports what it was before.

    Without this a test asserting English text depends on the host's language
    preference: it passes on an en-US runner and fails anywhere else for a reason that
    has nothing to do with what it measures. The local ARM64 VM is zh-TW and returned
    Chinese glyphs for an English line -- with both recognisers installed, so it was the
    preference and not a missing pack.

    The default file is written by PowerOCR rather than by this code: only the one field
    is patched, so the rest of the schema stays whatever the installed version says it
    is. The key is matched case-insensitively for the same reason -- the serializer's
    naming policy is not this harness's business.
    """
    if not SETTINGS_PATH.exists():
        seeder = subprocess.Popen([str(executable), str(os.getpid())])
        try:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not SETTINGS_PATH.exists():
                time.sleep(0.3)
        finally:
            seeder.terminate()
            seeder.wait(timeout=10)
        sweep()
    if not SETTINGS_PATH.exists():
        return None

    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
    properties = data.setdefault("properties", {})
    key = next((k for k in properties if k.lower() == "preferredlanguage"), "PreferredLanguage")
    previous = properties.get(key)
    properties[key] = OCR_LANGUAGE
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return previous


def signal_show_event() -> bool:
    """Opens PowerOCR's named event and sets it, which raises the overlay."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    EVENT_MODIFY_STATE = 0x0002
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def wait_for_overlay(timeout: float = 15.0):
    """Waits for a visible PowerOCR window and returns the list, or []."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        overlays = visible_overlay_windows()
        if overlays:
            return overlays
        time.sleep(0.25)
    return []


def wait_for_no_overlay(timeout: float = 10.0) -> bool:
    """True once PowerOCR has no visible window left."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not visible_overlay_windows():
            return True
        time.sleep(0.25)
    return not visible_overlay_windows()


def sweep() -> None:
    from wintegrate.apps import sweep_processes_verified

    sweep_processes_verified([PROCESS])


def clipboard_calls():
    """Clipboard entry points with their signatures declared.

    The argtypes are not decoration. Without `GetClipboardData.restype`, ctypes converts
    the returned handle as `c_int`, so on 64-bit any handle above 2^31 is truncated,
    `GlobalLock` is handed a bogus value, and reading the string it points at takes down
    the interpreter with an access violation. It looked intermittent because a small
    handle value survives the truncation.
    """
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.EmptyClipboard.argtypes = []
    user.EmptyClipboard.restype = wintypes.BOOL
    user.CloseClipboard.argtypes = []
    user.CloseClipboard.restype = wintypes.BOOL
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalUnlock.restype = wintypes.BOOL
    return user, kernel


def clear_clipboard() -> None:
    user, _ = clipboard_calls()
    if user.OpenClipboard(None):
        try:
            user.EmptyClipboard()
        finally:
            user.CloseClipboard()


def wait_for_clipboard_text(timeout: float = 20.0) -> str | None:
    """Waits for PowerOCR to publish its result.

    A synchronisation point, not an assertion: it is how a test knows OCR has finished
    rather than sleeping a guessed interval.
    """
    CF_UNICODETEXT = 13
    user, kernel = clipboard_calls()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user.OpenClipboard(None):
            try:
                handle = user.GetClipboardData(CF_UNICODETEXT)
                if handle:
                    pointer = kernel.GlobalLock(handle)
                    if pointer:
                        try:
                            text = ctypes.wstring_at(pointer)
                        finally:
                            kernel.GlobalUnlock(handle)
                        if text and text.strip():
                            return text
            finally:
                user.CloseClipboard()
        time.sleep(0.2)
    return None


def normalise(text: str) -> str:
    """Compares words, not layout.

    Whitespace and case are the two things a recogniser is entitled to render
    differently from the source without being wrong about the region it read. Every other
    difference -- a missing word, a partial word, a word from outside the selection --
    still fails the comparison.
    """
    return re.sub(r"\s+", " ", text).strip().upper()
