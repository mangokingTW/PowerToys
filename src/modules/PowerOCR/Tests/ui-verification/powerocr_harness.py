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
from ctypes import wintypes
from pathlib import Path

from wintegrate import NOTEPAD, Mouse, Window, WindowCensus, get_process_image_name
from wintegrate.interop import GUITHREADINFO, RECT, user32

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


# --- source windows, and dragging a band over them ---


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def caret(hwnd: int) -> tuple[int, int, int] | None:
    """The caret as (x, top, bottom) in screen coordinates, or None.

    `GetGUIThreadInfo` reports it in the client coordinates of whichever window owns
    the caret, which is not necessarily the window that was asked about.
    """
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return None
    rect = info.rcCaret
    owner = info.hwndCaret or hwnd
    top = _POINT(rect.left, rect.top)
    bottom = _POINT(rect.right, rect.bottom)
    user32.ClientToScreen(owner, ctypes.byref(top))
    user32.ClientToScreen(owner, ctypes.byref(bottom))
    if bottom.y <= top.y:
        return None
    return (top.x, top.y, bottom.y)


def source_with_text(rect, text: str):
    """A Notepad at a known rectangle holding `text`, with the first line's geometry.

    The text goes in through one `SetValue`, not through keystrokes. Typing it was
    the source of two separate problems:

    - the line break arrived out of order. Typing line 1, sending "\n", then typing
      line 2 under an `ime_mode` block produced
      'Harbour lights at duskFerries cross the water\r' -- the break at the end
      instead of between the lines. `ime_mode` sets the conversion mode with a sent
      message, which does not queue behind already-injected key input.
    - reading the caret twice to locate two lines was flaky: the same sequence that
      gives 171 -> 186 in isolation gave 171 -> 171 inside the test.

    Neither is interesting here. One SetValue and one caret read remove both: an
    empty editor puts the caret at the text origin, which is the first line, and
    every line below it is one caret-height further down.
    """
    _proc, window = Window.launch_and_discover(
        ["notepad.exe"],
        # 60s, not 30: a packaged app's cold start on ARM64 has been measured past
        # 12s, and 30 was not enough under the load of a whole suite.
        timeout=60.0,
        process_names=NOTEPAD.process_names,
        window_classes=NOTEPAD.window_classes,
    )
    window.move_and_resize(*rect)
    window.set_foreground(verify=False)
    time.sleep(0.6)
    editor = window.find_text_input(timeout=60.0)
    # SetValue, not Ctrl+A and Delete: the keystroke version does not clear this
    # control -- 261 characters went to 234 when it was measured.
    editor.set_value_verified("")
    editor.set_focus()
    time.sleep(0.3)

    origin = caret(window.hwnd)
    assert origin, "could not read the caret in the empty editor"
    line_height = origin[2] - origin[1]
    assert line_height > 4, f"implausible line height {line_height} from caret {origin}"

    editor.set_value_verified(text)
    time.sleep(0.5)

    held = editor.get_value() or ""
    expected_lines = [line for line in text.splitlines() if line.strip()]
    for line in expected_lines:
        assert normalise(line) in normalise(held), f"the source does not hold {line!r}: {held!a}"
    assert len(held.strip().splitlines()) == len(expected_lines), (
        f"the source is not {len(expected_lines)} lines: {held!a}"
    )

    second = (origin[0], origin[1] + line_height, origin[2] + line_height)
    print(f"line 1 {origin}, line height {line_height}, line 2 {second}")
    return window, editor, origin, second


def drag_band(mouse: Mouse, editor, first, bottom: int, window_x: int) -> str | None:
    """Drags a band from above the first line down to `bottom`, and returns the result."""
    _left, _top, right, _bottom = editor.bounding_rectangle

    # Left of the first glyph, and still inside the window. Measured origins differ:
    # the ARM64 VM puts the text at x=102, the x64 runner at x=92 with the window at
    # x=80 -- so a fixed 14px margin landed at 78, outside the window, and the band
    # caught the window border. It came back as '- Harbour lights at dusk\r\n:
    # Ferries cross the water': one punctuation mark per line, from a vertical line
    # in the band's left column.
    #
    # Vertically it stops at `bottom`. Running past the editor's own bottom is what
    # once swept Notepad's status bar into the result.
    text_left = first[0]
    start_x = max(window_x + 6, text_left - 10)
    assert start_x < text_left, (
        f"the band starts at {start_x}, not left of the first glyph at {text_left}"
    )

    clear_clipboard()
    mouse.move(start_x, first[1] - 4, steps=6, delay=0.02)
    time.sleep(0.3)
    mouse.down()
    mouse.move(right - 2, bottom + 4, steps=18, delay=0.03)
    time.sleep(0.5)
    mouse.up()
    return wait_for_clipboard_text(timeout=20.0)
