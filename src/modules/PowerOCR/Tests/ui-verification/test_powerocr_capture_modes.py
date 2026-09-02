"""Single-line mode: the same selection, formatted two ways.

The checklist's capture-mode line reads *"Single-line mode: activate Single-line,
click a single line of text; verify one line is on the clipboard"*. The button's own
accessible name is **"Format result as a single line"**, which is a different thing:
the mode is about how a multi-line result is joined, not about which line gets
picked.

So this drags across two lines twice -- once with the mode off, once on -- and
asserts the difference. Differential on purpose: an assertion about one run cannot
tell "the mode works" from "the recogniser happened to return one line", and the two
runs share everything except the toggle.

Not covered here, and measured rather than assumed: clicking a word without dragging
produced nothing at all. Five deliveries against five freshly raised overlays, the
cursor inside the same word each time --

    Mouse.click()                    nothing
    move, down, hold 0.35s, up       nothing
    tiny drag (6px)                  nothing
    drag across the whole word       'Kestrel'
    click 8px lower                  nothing

-- so on the shipped build the clicked-word path did not yield a result within 12s
by any gesture tried, while a drag over the same word did. That is one of the
checklist's unchecked lines, and it is a question for whoever knows the intended
gesture rather than a claim about the product.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import pytest
from wintegrate import NOTEPAD, Mouse, UiaElement, Window
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import GUITHREADINFO, user32

import powerocr_harness as h

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")

#: Two lines of ordinary words. Ordinary specifically: a recogniser segments an
#: unfamiliar token wherever it likes, and "WINTEGRATE" once came back as
#: "W INTEGRATE".
FIRST_LINE = "Harbour lights at dusk"
SECOND_LINE = "Ferries cross the water"

user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _caret(hwnd: int) -> tuple[int, int, int] | None:
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


def _source_with_two_lines(rect):
    """A Notepad holding the two lines, with both lines' geometry.

    The text goes in through one `SetValue`, not through keystrokes. Typing it was
    the source of two separate problems:

    - the line break arrived out of order. Typing line 1, sending "\n", then typing
      line 2 under an `ime_mode` block produced
      'Harbour lights at duskFerries cross the water\r' -- the break at the end
      instead of between the lines. `ime_mode` sets the conversion mode with a sent
      message, which does not queue behind already-injected key input.
    - reading the caret twice to locate both lines was flaky: the same sequence that
      gives 171 -> 186 in isolation gave 171 -> 171 inside the test.

    Neither is interesting here, and neither is what this test is about. One
    SetValue and one caret read remove both.
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

    # An empty editor puts the caret at the text origin, which is the first line.
    origin = _caret(window.hwnd)
    assert origin, "could not read the caret in the empty editor"
    line_height = origin[2] - origin[1]
    assert line_height > 4, f"implausible line height {line_height} from caret {origin}"

    editor.set_value_verified(f"{FIRST_LINE}\r\n{SECOND_LINE}")
    time.sleep(0.5)

    held = editor.get_value() or ""
    for expected in (FIRST_LINE, SECOND_LINE):
        assert h.normalise(expected) in h.normalise(held), (
            f"the source does not hold {expected!r}: {held!a}"
        )
    assert len(held.strip().splitlines()) == 2, f"the source is not two lines: {held!a}"

    first = origin
    second = (origin[0], origin[1] + line_height, origin[2] + line_height)
    print(f"line 1 {first}, line height {line_height}, line 2 {second}")
    return window, editor, first, second


def _extract_both_lines(mouse: Mouse, editor, first, second) -> str | None:
    """Drags a band over both lines and returns what PowerOCR published."""
    _left, _top, right, _bottom = editor.bounding_rectangle

    # The left edge comes from the text origin -- the caret's x in an empty editor --
    # and not from the editor's rectangle. `editor_left + 2` clipped the first glyph
    # on x64 while working on ARM64: both runs came back as 'arbour lights at dusk',
    # missing the H. The editor's rectangle and the first glyph are not the same
    # place, and the difference is smaller than the padding either arch happens to
    # use.
    #
    # Vertically from just above the first line to just below the second. Running past
    # the editor's *bottom* is what once swept Notepad's status bar into the result,
    # so the band stops at the second line.
    text_left = first[0]
    start_x, start_y = max(0, text_left - 14), first[1] - 4
    end_x, end_y = right - 2, second[2] + 4
    assert start_x < text_left, (
        f"the band starts at {start_x}, not left of the first glyph at {text_left}"
    )

    h.clear_clipboard()
    mouse.move(start_x, start_y, steps=6, delay=0.02)
    time.sleep(0.3)
    mouse.down()
    mouse.move(end_x, end_y, steps=18, delay=0.03)
    time.sleep(0.5)
    mouse.up()
    return h.wait_for_clipboard_text(timeout=20.0)


def test_single_line_mode_joins_the_result_into_one_line(recording):
    """*Single-line mode: verify one line is on the clipboard.*

    Both lines are selected each time; only the toggle changes. With the mode off the
    result keeps the break between them, and with it on the result is one line -- so
    the assertion is on the difference the mode makes, which is what the button says
    it does.
    """
    executable = h.powerocr_executable()
    h.sweep()
    sweep_processes_verified(["notepad.exe", "Notepad.exe"])
    was = h.pin_ocr_language(executable)
    print(f"OCR language pinned to {h.OCR_LANGUAGE!r} (was {was!r})")

    results = {}

    # The source window is opened once and reused for both runs. Opening and killing
    # it per iteration timed out on ARM64 -- notepad.exe did not appear within 30s --
    # because a packaged app's cold start there is slow and a kill followed straight
    # away by a relaunch makes it slower. Only PowerOCR has to be restarted: the
    # overlay closes itself once a capture completes.
    h.sweep()
    sweep_processes_verified(["notepad.exe", "Notepad.exe"])
    time.sleep(0.8)
    _window, editor, first, second = _source_with_two_lines((80, 80, 760, 260))

    for single_line in (False, True):
        h.sweep()
        time.sleep(0.5)
        powerocr = subprocess.Popen([str(executable), str(os.getpid())])
        try:
            time.sleep(2.0)
            assert h.signal_show_event(), f"could not open {h.SHOW_EVENT_NAME}"
            overlays = h.wait_for_overlay(timeout=15.0)
            assert overlays, "the overlay did not come up"

            overlay = Window(overlays[0][0])
            toggle = UiaElement.from_handle(overlay.hwnd).find_all(automation_id=h.SINGLE_LINE_ID)[
                0
            ]
            assert toggle.set_toggle_verified(single_line), (
                f"Single-line mode would not report itself {'on' if single_line else 'off'}"
            )
            assert toggle.toggle_state == (1 if single_line else 0), (
                f"Single-line mode reports {toggle.toggle_state}, not {1 if single_line else 0}"
            )

            extracted = _extract_both_lines(Mouse(), editor, first, second)
            assert extracted is not None, (
                f"nothing was published within 20s with single-line "
                f"{'on' if single_line else 'off'}"
            )
            results[single_line] = extracted
            print(f"single_line={single_line}: {extracted!a}")
        finally:
            powerocr.terminate()
            h.sweep()

    sweep_processes_verified(["notepad.exe", "Notepad.exe"])
    off, on = results[False], results[True]

    # Both lines have to be in both results, or the selection missed something and the
    # comparison below would be about the drag rather than about the mode.
    for label, text in (("off", off), ("on", on)):
        for line in (FIRST_LINE, SECOND_LINE):
            assert h.normalise(line) in h.normalise(text), (
                f"with single-line {label} the result is missing {line!r}: {text!a}"
            )

    assert len(on.strip().splitlines()) == 1, (
        f"single-line mode on: the result still spans {len(on.strip().splitlines())} lines: {on!a}"
    )
    assert len(off.strip().splitlines()) > 1, (
        f"single-line mode off: the result is already one line, so turning the mode on "
        f"cannot be shown to change anything: {off!a}"
    )
