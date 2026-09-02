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

import os
import subprocess
import sys
import time

import pytest
from wintegrate import NOTEPAD, ImeConversion, Mouse, Window
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import SM_CXSCREEN, SM_CYSCREEN, user32

import powerocr_harness as h

#: OCR has to read these back verbatim, so they are ordinary English words on one

TEXT_INSIDE = "Selected region inside the band"

TEXT_OUTSIDE = "Excluded sentence further down"

#: The two source windows are stacked with a gap wide enough that the band's lower

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
    #
    # Retried, because injection can drop the tail. ARM64 under the load of the whole
    # suite left 'Selected region inside ' in the editor -- "the band" missing -- and
    # the check below is what caught it. Two attempts, and the failure names what
    # actually landed rather than reporting a mismatch further down the test.
    typed = ""
    for attempt in (1, 2):
        editor.set_value_verified("")
        editor.set_focus()
        with win.ime_mode(ImeConversion.ALPHANUMERIC):
            editor.send_physical_keys(text)
        time.sleep(0.8)
        typed = editor.get_value() or ""
        if h.normalise(typed) == h.normalise(text):
            break
        print(f"attempt {attempt}: the editor holds {typed!a}, retyping")

    assert h.normalise(typed) == h.normalise(text), (
        f"the source window does not hold the text this test asserts against after "
        f"two attempts: wanted {text!r}, editor holds {typed!a}"
    )
    return win


def test_pointer_drag_bounds_the_extracted_text(recording):
    """Drag a region over one window, and get back exactly that window's text."""
    executable = h.powerocr_executable()
    sweep_processes_verified([h.PROCESS, "notepad.exe", "Notepad.exe"])

    screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
    inside_rect, outside_rect = _layout(screen_w, screen_h)
    needed = outside_rect[1] + outside_rect[3] + MARGIN
    assert screen_h >= needed, (
        f"the desktop is {screen_w}x{screen_h}; the two source windows plus the "
        f"{GAP}px gap need {needed}px of height"
    )

    was = h.pin_ocr_language(executable)
    print(f"OCR language pinned to {h.OCR_LANGUAGE!r} (was {was!r})")

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

        h.clear_clipboard()

        # The module's own checklist, "Activation paths": *launch the Text Extractor
        # executable directly (standalone mode); verify the overlay appears on
        # activation*. Asserted here rather than inferred from OCR succeeding later --
        # if the overlay never came up, the only symptom downstream would be an empty
        # clipboard, which says nothing about why.
        already_up = h.visible_overlay_windows()
        assert not already_up, (
            f"PowerOCR already has a visible window before its show event was "
            f"signalled, so this cannot attribute the overlay to the activation: "
            f"{already_up}"
        )
        assert h.signal_show_event(), (
            f"could not open {h.SHOW_EVENT_NAME}; PowerOCR is not waiting on its "
            f"show event, so the overlay was never raised"
        )

        deadline = time.monotonic() + 15.0
        overlays: list = []
        while time.monotonic() < deadline:
            overlays = h.visible_overlay_windows()
            if overlays:
                break
            time.sleep(0.25)
        assert overlays, f"no visible window appeared within 15s of signalling {h.SHOW_EVENT_NAME}"

        # One overlay per display, so on a multi-monitor host there is more than one;
        # what matters is that the primary display is covered by a topmost one.
        #
        # Within a couple of pixels, deliberately: the overlay's left edge is not
        # always 0. Eight samples on a 1024x768 runner:
        #
        #   0.101.2362.0 (release)     x64 0, arm64 0, x64 0, arm64 **1**
        #   0.0.1.0 (build from main)  x64 1, arm64 1, x64 1, arm64 1
        #
        # An earlier version of this comment called that a difference between the two
        # builds. The release/arm64 sample showing 1 says that is not supported: the
        # value moves between runs of the same installer, so whatever produces it is
        # not settled by which build is under test. The rect is printed on every run,
        # so a real change in it stays visible; asserting the exact edge would just
        # fail runs for a reason unrelated to region selection.
        inset = h.OVERLAY_EDGE_TOLERANCE
        primary = [
            entry
            for entry in overlays
            if entry[1][0] <= inset
            and entry[1][1] <= inset
            and entry[1][2] >= screen_w - inset
            and entry[1][3] >= screen_h - inset
        ]
        assert primary, (
            f"no overlay covers the {screen_w}x{screen_h} primary display to within "
            f"{inset}px; visible PowerOCR windows were {overlays}"
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

        extracted = h.wait_for_clipboard_text(timeout=20.0)
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

            assert h.normalise(pasted) == h.normalise(TEXT_INSIDE), (
                f"the selection did not bound the OCR input.\n"
                f"  expected: {h.normalise(TEXT_INSIDE)!a}\n"
                f"  got:      {h.normalise(pasted)!a}\n"
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
        sweep_processes_verified([h.PROCESS, "notepad.exe", "Notepad.exe"])
