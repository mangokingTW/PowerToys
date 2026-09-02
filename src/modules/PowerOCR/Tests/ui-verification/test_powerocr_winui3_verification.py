"""Automated verification for PowerToys Text Extractor (PowerOCR WinUI 3).

microsoft/PowerToys#49431 lists 16 validation items as BLOCKED, because they need
an attached interactive desktop, WinAppDriver, and a real pointer drag. This runs
the interesting one of those on a stock GitHub-hosted runner with none of them:
summon the overlay, drag a selection rectangle across it, and check what came out.

What makes it a test rather than a demo is that it proves the **bounds** of the
selection, not merely that OCR produced something.

Two source windows are placed at known rectangles with a deliberate gap between
them, each holding a different line of text. The drag covers the first window and
stops inside the gap. The extracted text is then asserted to equal the first
window's line *exactly*, and that single equality fails in both directions:

- too small a selection, or one offset from where it was asked to go, loses part
  of the line and the strings differ (an earlier version of this test selected a
  region 3 characters short on the left and read back "erministic UI Automation
  by wintegrate" -- which a "text is not empty" assertion accepted);
- too large a selection reaches the second window and adds a line that is not
  in the expectation.

The extracted text is read back through UI Automation from a destination Notepad,
not from the clipboard: the clipboard is what PowerOCR writes, so asserting on it
would only prove PowerOCR talked to itself. Reading the editor proves the text
arrived somewhere a person would see it, which is also what the recording shows.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from wintegrate import NOTEPAD, ImeConversion, Mouse, Window, WindowCensus, get_process_image_name
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import RECT, SM_CXSCREEN, SM_CYSCREEN, user32

PROCESS = "PowerToys.PowerOCR.exe"

#: PowerOCR waits on this event; setting it is what the PowerToys runner does when
#: the Text Extractor hotkey fires. Signalling it directly means the test does not
#: depend on a hotkey reaching a detached desktop, which is one of the blockers.
SHOW_EVENT_NAME = r"Local\PowerOCREvent-dc864e06-e1af-4ecc-9078-f98bee745e3a"

#: OCR has to read these back verbatim, so they are ordinary English words on one
#: line: no glyphs that trade places under a recogniser (0/O, 1/l/I), and no
#: line-break policy in play.
#:
#: Ordinary words specifically. An earlier version used "WINTEGRATE", which came
#: back as "W INTEGRATE" -- a recogniser segments an unfamiliar token wherever it
#: likes, and that is not a fact about the region that was selected.
TEXT_INSIDE = "Selected region inside the band"
TEXT_OUTSIDE = "Excluded sentence further down"

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

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

#: The two source windows are stacked with a gap wide enough that the band's lower
#: edge lands nowhere near a glyph. Derived from the desktop size rather than
#: hardcoded -- the hosted runners are 1024x768 and the local ARM64 VM is 800x600 --
#: but fully determined once the size is known, which is what the drag is checked
#: against.
MARGIN = 40
WINDOW_HEIGHT = 200
GAP = 80


def _layout(screen_w: int, screen_h: int):
    """(inside, outside) window rectangles as (x, y, w, h)."""
    width = screen_w - 2 * MARGIN
    inside = (MARGIN, MARGIN, width, WINDOW_HEIGHT)
    outside = (MARGIN, MARGIN + WINDOW_HEIGHT + GAP, width, WINDOW_HEIGHT)
    return inside, outside


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")


def _powerocr_executable() -> Path:
    """Locates PowerOCR across the installer's layouts."""
    custom = os.environ.get("POWERTOYS_POWEROCR_PATH")
    if custom and Path(custom).exists():
        return Path(custom)

    roots = [
        Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")) / "PowerToys",
        Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
        / "Programs"
        / "PowerToys",
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


def _visible_overlay_windows() -> list[tuple[int, tuple[int, int, int, int], bool, str]]:
    """Every visible top-level window owned by PowerOCR, as (hwnd, rect, topmost, title).

    Enumerated through WindowCensus. `Window.find_all` does not exist, and calling it
    inside a bare `except Exception` is how an earlier version of this file reported
    "overlay not detected" on every run while the real error was an AttributeError.

    Matched on ownership and geometry rather than on window class. The class carries a
    per-run GUID (`HwndWrapper[PowerToys.PowerOCR;;<guid>]` on the shipped WPF build,
    `WinUIDesktopWin32WindowClass` after the migration), so a class filter would be a
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


def _pin_ocr_language(executable: Path) -> str | None:
    """Pins the recogniser to English, and reports what it was before.

    Without this the test asserts English text while PowerOCR picks its engine from
    the host's language preference: it passes on an en-US runner and fails anywhere
    else for a reason that has nothing to do with region selection. The local ARM64
    VM is zh-TW and returned Chinese glyphs for this line -- with both en-US and
    zh-TW recognisers installed, so it was the preference and not a missing pack.

    The default file is written by PowerOCR rather than by this test: only the one
    field is patched, so the rest of the schema stays whatever the installed version
    says it is. The key is matched case-insensitively for the same reason -- the
    serializer's naming policy is not this test's business.
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
        sweep_processes_verified([PROCESS])
    if not SETTINGS_PATH.exists():
        return None

    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
    properties = data.setdefault("properties", {})
    key = next((k for k in properties if k.lower() == "preferredlanguage"), "PreferredLanguage")
    previous = properties.get(key)
    properties[key] = OCR_LANGUAGE
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return previous


def _signal_show_event() -> bool:
    """Opens PowerOCR's named event and sets it, which raises the overlay."""
    import ctypes
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


def _clipboard_calls():
    """Clipboard entry points with their signatures declared.

    The argtypes are not decoration. Without `GetClipboardData.restype`, ctypes
    converts the returned handle as `c_int`, so on 64-bit any handle above 2^31 is
    truncated, `GlobalLock` is handed a bogus value, and reading the string it
    points at takes down the interpreter with an access violation. It looked
    intermittent because a small handle value survives the truncation.
    """
    import ctypes
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
    return user, kernel, ctypes


def _clear_clipboard() -> None:
    user, _, _ = _clipboard_calls()
    if user.OpenClipboard(None):
        try:
            user.EmptyClipboard()
        finally:
            user.CloseClipboard()


def _wait_for_clipboard_text(timeout: float = 20.0) -> str | None:
    """Waits for PowerOCR to publish its result.

    Only a synchronisation point: the assertion is made on what reaches Notepad,
    not on this. Polling the clipboard is how the test knows OCR has finished
    rather than sleeping a guessed interval and pasting nothing.
    """
    CF_UNICODETEXT = 13
    user, kernel, ctypes_mod = _clipboard_calls()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user.OpenClipboard(None):
            try:
                handle = user.GetClipboardData(CF_UNICODETEXT)
                if handle:
                    pointer = kernel.GlobalLock(handle)
                    if pointer:
                        try:
                            text = ctypes_mod.wstring_at(pointer)
                        finally:
                            kernel.GlobalUnlock(handle)
                        if text and text.strip():
                            return text
            finally:
                user.CloseClipboard()
        time.sleep(0.2)
    return None


def _normalise(text: str) -> str:
    """Compares words, not layout.

    Whitespace and case are the two things a recogniser is entitled to render
    differently from the source without being wrong about the region it read. Every
    other difference -- a missing word, a partial word, a word from the window that
    should have been outside the selection -- still fails the comparison.
    """
    return re.sub(r"\s+", " ", text).strip().upper()


def _open_source_window(text: str, rect: tuple[int, int, int, int]) -> Window:
    """A Notepad at a fixed rectangle holding one line of known text."""
    _proc, win = Window.launch_and_discover(
        ["notepad.exe"],
        timeout=30.0,
        process_names=NOTEPAD.process_names,
        window_classes=NOTEPAD.window_classes,
    )
    win.move_and_resize(*rect)
    win.set_foreground(verify=False)
    time.sleep(0.6)

    # Notepad is discoverable before its editor exists: a launch here handed back a
    # window whose title was empty and which held no text input at all, and
    # find_text_input then spent its whole timeout on the wrong window.
    editor = win.find_text_input(timeout=30.0)
    editor.set_focus()

    # SetValue through UI Automation, not Ctrl+A followed by Delete. The keystroke
    # version does not clear this control: measured on Win11 Notepad, a 261-character
    # editor went to 234 -- one line removed, the rest left in place, and the line
    # this test asserts against then typed underneath it.
    #
    # Clearing is needed at all because Notepad restores the previous session's tabs,
    # so a freshly launched window is only empty on a machine that has never used it.
    # A hosted runner is clean and this is a no-op there.
    editor.set_value_verified("")

    # send_physical_keys under an alphanumeric IME mode, not send_keys: real virtual
    # keys carry the right scan codes, so the recording's keyboard overlay shows the
    # keys that were actually pressed instead of mislabelling injected characters.
    with win.ime_mode(ImeConversion.ALPHANUMERIC):
        editor.send_physical_keys(text)
    time.sleep(0.8)

    typed = editor.get_value()
    assert _normalise(typed) == _normalise(text), (
        f"the source window does not hold the text this test asserts against: "
        f"wanted {text!r}, editor holds {typed!a}"
    )
    return win


def test_pointer_drag_bounds_the_extracted_text(recording):
    """Drag a region over one window, and get back exactly that window's text."""
    executable = _powerocr_executable()
    sweep_processes_verified([PROCESS, "notepad.exe", "Notepad.exe"])

    screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
    inside_rect, outside_rect = _layout(screen_w, screen_h)
    needed = outside_rect[1] + outside_rect[3] + MARGIN
    assert screen_h >= needed, (
        f"the desktop is {screen_w}x{screen_h}; the two source windows plus the "
        f"{GAP}px gap need {needed}px of height"
    )

    was = _pin_ocr_language(executable)
    print(f"OCR language pinned to {OCR_LANGUAGE!r} (was {was!r})")

    powerocr = subprocess.Popen([str(executable), str(os.getpid())])
    try:
        # PowerOCR has to be up before its event exists to be opened.
        time.sleep(2.0)

        inside_win = _open_source_window(TEXT_INSIDE, inside_rect)
        # Opened for its pixels, not for its handle: it is the text that must not
        # end up in the extraction.
        _open_source_window(TEXT_OUTSIDE, outside_rect)

        # The band is the top half of the source editor: a real sub-rectangle, and
        # bounded by the editor rather than by the window.
        #
        # Not the window rectangle, and not one pixel past the editor either. An
        # earlier version dragged from 8px above the editor down into the gap below
        # the window, which swept in Notepad's own tab title and status bar -- the
        # extracted text came back with "100% WINDOWS (CRLF) UTF-8" appended, which
        # is a correct reading of a wrongly chosen region.
        #
        # The line sits in the first ~20px of a ~150px editor, so the top half holds
        # all of it with room to spare, and no glyph is cut.
        editor_rect = inside_win.find_text_input().bounding_rectangle
        assert editor_rect is not None, "could not measure the source editor rectangle"
        left, top, right, bottom = editor_rect
        start_x, start_y = left, top
        end_x, end_y = right, top + (bottom - top) // 2
        assert end_y < outside_rect[1], "the band would reach the outside window"
        assert end_y > top + 30, (
            f"the source editor is only {bottom - top}px tall; half of it may clip the line"
        )

        _clear_clipboard()

        # The module's own checklist, "Activation paths": *launch the Text Extractor
        # executable directly (standalone mode); verify the overlay appears on
        # activation*. Asserted here rather than inferred from OCR succeeding later --
        # if the overlay never came up, the only symptom downstream would be an empty
        # clipboard, which says nothing about why.
        already_up = _visible_overlay_windows()
        assert not already_up, (
            f"PowerOCR already has a visible window before its show event was "
            f"signalled, so this cannot attribute the overlay to the activation: "
            f"{already_up}"
        )
        assert _signal_show_event(), (
            f"could not open {SHOW_EVENT_NAME}; PowerOCR is not waiting on its "
            f"show event, so the overlay was never raised"
        )

        deadline = time.monotonic() + 15.0
        overlays: list = []
        while time.monotonic() < deadline:
            overlays = _visible_overlay_windows()
            if overlays:
                break
            time.sleep(0.25)
        assert overlays, f"no visible window appeared within 15s of signalling {SHOW_EVENT_NAME}"

        # One overlay per display, so on a multi-monitor host there is more than one;
        # what matters is that the primary display is covered by a topmost one.
        primary = [
            entry
            for entry in overlays
            if entry[1][0] <= 0
            and entry[1][1] <= 0
            and entry[1][2] >= screen_w
            and entry[1][3] >= screen_h
        ]
        assert primary, (
            f"the overlay does not cover the {screen_w}x{screen_h} primary display; "
            f"visible PowerOCR windows were {overlays}"
        )
        assert primary[0][2], (
            f"the overlay covering the primary display is not topmost, so anything "
            f"drawn over it would be captured instead: {primary[0]}"
        )
        print(
            f"overlay up: hwnd={primary[0][0]:#x} rect={primary[0][1]} "
            f"topmost={primary[0][2]} title={primary[0][3]!r}"
        )
        time.sleep(1.0)

        mouse = Mouse()
        mouse.move(start_x, start_y, steps=8, delay=0.02)
        time.sleep(0.4)
        mouse.down()
        # Interpolated, not a jump: the WinUI 3 overlay redraws its mask from
        # WM_MOUSEMOVE, so a single move to the end point leaves no selection.
        mouse.move(end_x, end_y, steps=25, delay=0.03)
        time.sleep(0.8)
        mouse.up()

        extracted = _wait_for_clipboard_text(timeout=20.0)
        assert extracted is not None, (
            "PowerOCR published no text within 20s of the drag being released"
        )
        # ascii(), not !r: a recogniser can return glyphs the console's code
        # page cannot encode, and printing the diagnostic must not be the
        # thing that fails the test. Seen on a zh-TW host, where the OCR
        # engine read the English source as Chinese and the print raised
        # UnicodeEncodeError from cp950.
        print(f"PowerOCR extracted: {extracted!a}")

        # Deliberately not closing the source windows: their content is unsaved, so
        # WM_CLOSE puts up a "save changes?" dialog and the run stalls behind it. The
        # sweep in the finally block terminates them instead, and the destination
        # window opens on top of them, which is all the recording needs.

        _proc, dest_win = Window.launch_and_discover(
            ["notepad.exe"],
            timeout=30.0,
            process_names=NOTEPAD.process_names,
            window_classes=NOTEPAD.window_classes,
        )
        dest_win.move_and_resize(MARGIN, MARGIN, screen_w - 2 * MARGIN, screen_h - 2 * MARGIN - 60)
        with dest_win.foreground(verify=False):
            editor = dest_win.find_text_input(timeout=30.0)
            editor.set_focus()
            # Cleared for the same session-restore reason as the sources: what is
            # read back has to be the paste and nothing else.
            editor.set_value_verified("")
            editor.set_focus()
            editor.send_keys("^v")
            time.sleep(1.5)

            pasted = editor.get_value()
            print(f"read back from Notepad: {pasted!a}")

            assert _normalise(pasted) == _normalise(TEXT_INSIDE), (
                f"the selection did not bound the OCR input.\n"
                f"  expected: {_normalise(TEXT_INSIDE)!a}\n"
                f"  got:      {_normalise(pasted)!a}\n"
                f"  dragged:  ({start_x}, {start_y}) -> ({end_x}, {end_y})\n"
                f"  the outside window held {TEXT_OUTSIDE!r} at y="
                f"{outside_rect[1]}; text from it appearing above means the "
                f"band was taller than it was dragged.\n"
                f"  if the text came back as another script entirely, the host "
                f"is missing the en-US OCR language "
                f"(Language.OCR~~~en-US~0.0.1.0) and the recogniser fell back "
                f"to whichever one is installed -- seen on a zh-TW VM, which "
                f"returned Chinese glyphs for this line"
            )
    finally:
        powerocr.terminate()
        sweep_processes_verified([PROCESS, "notepad.exe", "Notepad.exe"])
