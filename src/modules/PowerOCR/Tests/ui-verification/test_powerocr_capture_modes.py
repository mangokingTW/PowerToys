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

import os
import subprocess
import sys
import time

import pytest
from wintegrate import Mouse, UiaElement, Window
from wintegrate.apps import sweep_processes_verified

import powerocr_harness as h

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")

#: Two lines of ordinary words. Ordinary specifically: a recogniser segments an
#: unfamiliar token wherever it likes, and "WINTEGRATE" once came back as
#: "W INTEGRATE".
#: The source window, named so the band and the window cannot drift apart.
SOURCE_RECT = (80, 80, 760, 260)

FIRST_LINE = "Harbour lights at dusk"
SECOND_LINE = "Ferries cross the water"

#: Three space-aligned rows, which is the cheapest thing on screen that looks like a
#: table. The cell text is deliberately not asserted -- a recogniser reading a
#: monospace grid returned 'ruit' for "Fruit" and 'App I e' for "Apple" -- so the
#: words only have to be distinct enough to keep the columns apart.
TABLE_ROWS = (
    "Fruit      Colour     Count",
    "Apple      Red        Three",
    "Lemon      Yellow     Seven",
)


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
    _window, editor, first, second = h.source_with_text(
        SOURCE_RECT, f"{FIRST_LINE}\r\n{SECOND_LINE}"
    )

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

            extracted = h.drag_band(Mouse(), editor, first, second[2], window_x=SOURCE_RECT[0])
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

    # Equality, not containment. Containment passed a result that had picked up a
    # stray mark: x64 returned '- Harbour lights at dusk\r\nFerries cross the water'
    # while ARM64 returned the same text without the leading '- ' in the same run. The
    # likeliest source is the source editor's text caret inside the band, drawn or not
    # depending on its blink phase, which a recogniser reads as a hyphen -- and a
    # containment check cannot see it at all.
    #
    # If this turns out flaky rather than fixed, that is worth knowing: the band's left
    # edge and the caret occupy the same column, and the answer is to take focus off the
    # source editor, not to loosen the assertion again.
    expected = h.normalise(f"{FIRST_LINE} {SECOND_LINE}")
    assert h.normalise(off) == expected, (
        f"with single-line off the result is not the two source lines: {off!a}"
    )
    assert h.normalise(on) == expected, (
        f"with single-line on the result is not the two source lines: {on!a}"
    )

    # normalise() collapses whitespace, so both are equal once normalised. The
    # difference the mode makes is in the line structure, checked on the raw strings.
    assert len(on.strip().splitlines()) == 1, (
        f"single-line mode on: the result still spans {len(on.strip().splitlines())} lines: {on!a}"
    )
    assert len(off.strip().splitlines()) > 1, (
        f"single-line mode off: the result is already one line, so turning the mode on "
        f"cannot be shown to change anything: {off!a}"
    )


def test_table_mode_returns_tab_separated_rows(recording):
    """*Table mode: activate Table mode, select a tabular region; verify
    tab-separated values are on the clipboard.*

    Differential again, and asserted on structure rather than on cell text. The
    recogniser misreads a monospace grid often enough that any equality on the
    cells would be testing the font: measured, "Fruit" came back as 'ruit' and
    "Apple" as 'App I e'. What table mode changes is unmistakable even so:

        table off: 'ruit\r\nApp I e\r\nLemon\r\nColour\r\nRed\r\nYellow\r\nCount\r\nSeven'
        table on:  'Fruit\t\tCount\r\nApp I e\tRed\r\nLemon\tYellow\tSeven'

    With the mode off the result is *column-major* -- every cell of column one,
    then column two -- because reading order across aligned columns is not
    line-major. With it on the result is one line per row, cells separated by tabs.
    """
    executable = h.powerocr_executable()
    h.sweep()
    sweep_processes_verified(["notepad.exe", "Notepad.exe"])
    was = h.pin_ocr_language(executable)
    print(f"OCR language pinned to {h.OCR_LANGUAGE!r} (was {was!r})")

    _window, editor, first, _second = h.source_with_text(SOURCE_RECT, "\r\n".join(TABLE_ROWS))
    line_height = first[2] - first[1]
    last_row_bottom = first[2] + line_height * (len(TABLE_ROWS) - 1)

    results = {}
    for table_on in (False, True):
        h.sweep()
        time.sleep(0.5)
        powerocr = subprocess.Popen([str(executable), str(os.getpid())])
        try:
            time.sleep(2.0)
            assert h.signal_show_event(), f"could not open {h.SHOW_EVENT_NAME}"
            overlays = h.wait_for_overlay(timeout=15.0)
            assert overlays, "the overlay did not come up"

            toggle = UiaElement.from_handle(Window(overlays[0][0]).hwnd).find_all(
                automation_id=h.TABLE_ID
            )[0]
            assert toggle.set_toggle_verified(table_on), (
                f"Table mode would not report itself {'on' if table_on else 'off'}"
            )

            extracted = h.drag_band(
                Mouse(), editor, first, last_row_bottom, window_x=SOURCE_RECT[0]
            )
            assert extracted is not None, (
                f"nothing was published within 20s with table {'on' if table_on else 'off'}"
            )
            results[table_on] = extracted
            print(
                f"table={table_on}: {extracted!a} "
                f"(tabs={extracted.count(chr(9))}, "
                f"lines={len(extracted.strip().splitlines())})"
            )
        finally:
            powerocr.terminate()
            h.sweep()

    sweep_processes_verified(["notepad.exe", "Notepad.exe"])
    off, on = results[False], results[True]

    assert "\t" not in off, (
        f"table mode off already produced tabs, so turning it on cannot be shown to "
        f"change anything: {off!a}"
    )
    assert "\t" in on, (
        f"table mode on produced no tab at all, which is what the checklist asks for: {on!a}"
    )
    assert len(on.strip().splitlines()) == len(TABLE_ROWS), (
        f"table mode on returned {len(on.strip().splitlines())} lines for "
        f"{len(TABLE_ROWS)} rows: {on!a}"
    )
    assert len(off.strip().splitlines()) > len(TABLE_ROWS), (
        f"table mode off returned {len(off.strip().splitlines())} lines, not more "
        f"than the {len(TABLE_ROWS)} rows -- the column-major reading this contrasts "
        f"with is not happening: {off!a}"
    )
