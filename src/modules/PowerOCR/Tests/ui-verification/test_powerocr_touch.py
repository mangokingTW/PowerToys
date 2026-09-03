"""The two checklist lines that need a finger, not a mouse.

The checklist item

    - [ ] Click a single word or character without dragging and verify the
          recognized token is copied to the clipboard.

is the one this suite has never been able to satisfy. Five mouse deliveries were
measured against five freshly raised overlays with the cursor inside the same
word -- `Mouse.click()`, move-down-hold-up, a 6px drag, and a click 8px lower --
and none of them produced anything within 12s, while a drag across the word
returned `Kestrel` every time. So the region path works and the click path does
not, from a mouse.

Touch is the input class that was never tried, and "click a single word" is a
finger gesture in origin: on a Surface, tapping a word is how you use this. That
is what these tests do.

They report rather than assume. A tap that produces no text fails with what the
clipboard held instead, because "touch cannot do it either" is a finding worth
having upstream -- it would mean the single-token path needs a gesture nobody has
identified, which is a product question rather than a harness one.

Touch is gated on `Touch.available()`, which measures delivery instead of
trusting the API: every injection call returns success on a host whose desktop is
covered by another window, and nothing arrives. When that happens these skip with
the reason, because a green run there would be a lie.
"""

from __future__ import annotations

import time

import powerocr_harness as harness
import pytest
from wintegrate import Touch
from wintegrate.element import UiaElement

# A word with no ambiguous glyphs, on its own line, away from the caret: a
# blinking caret merged into the R of "Region" once and the OCR read `Hegion`.
SINGLE_WORD = "Kestrel"
SOURCE_TEXT = f"{SINGLE_WORD}\nSecond line stays out of the way"

SOURCE_RECT = (80, 120, 700, 300)


@pytest.fixture
def touch():
    """One digitizer for the test, and a skip with the reason when it cannot deliver."""
    with Touch() as device:
        if not device.available():
            pytest.skip(
                "touch injection is not delivered on this host -- the API accepts "
                "every contact and no window receives one. A full-screen window "
                "owning the foreground is the usual cause; CI closes the ARM "
                "runner's onboarding screen before the GUI tests for this reason."
            )
        yield device


def _word_centre(origin, editor) -> tuple[int, int]:
    """A point inside the first word, derived from the caret rather than the control.

    The caret in an empty editor sits at the text origin, so the first line
    starts there. Deriving the point from the editor's rectangle instead has
    failed three times: three characters in, past the editor's bottom, and 2px
    inside the border where the first letter was clipped on x64 only.
    """
    x, top, bottom = origin
    # Half a line of headroom, then the vertical middle of the glyphs. Without
    # the headroom ARM64 lost every character with an ascender.
    y = (top + bottom) // 2
    # Into the middle of the word: one character in is not the word's centre, and
    # a tap at the very first pixel column lands in the margin.
    approx_char_width = max(6, (bottom - top) // 2)
    return (x + approx_char_width * len(SINGLE_WORD) // 2, y)


def test_a_tap_on_a_single_word_extracts_that_word(overlay, touch):
    """The unchecked checklist line: one word, tapped, no drag."""
    window, editor, origin, _second = harness.source_with_text(SOURCE_RECT, SOURCE_TEXT)
    try:
        harness.clear_clipboard()
        harness.signal_show_event()
        assert harness.wait_for_overlay(), "the overlay did not come up over the source"

        x, y = _word_centre(origin, editor)
        print(f"tapping ({x}, {y}); first line caret {origin}")
        assert touch.tap(x, y), "the tap was refused by the injection API"

        got = harness.wait_for_clipboard_text(timeout=12.0)
        assert got is not None, (
            f"a tap at ({x}, {y}) produced no clipboard text in 12s. The mouse "
            "cannot satisfy this line either -- five gestures were measured -- so "
            "this is a report about the single-token path, not about the tap."
        )
        assert harness.normalise(SINGLE_WORD) in harness.normalise(got), (
            f"tapped {SINGLE_WORD!r} and got {got!a}"
        )
    finally:
        harness.wait_for_no_overlay()
        window.close(force=True)


def test_a_touch_drag_selects_a_region(overlay, touch):
    """Region capture with a finger, which is how the overlay is used on a tablet.

    The mouse version of this is covered elsewhere; this asserts the same result
    through a different input class, because the overlay reads pointer input and
    a contact is not a mouse event -- WM_POINTER carries a different pointer type
    and Windows synthesises the mouse messages separately.
    """
    window, editor, origin, second = harness.source_with_text(SOURCE_RECT, SOURCE_TEXT)
    try:
        harness.clear_clipboard()
        harness.signal_show_event()
        assert harness.wait_for_overlay(), "the overlay did not come up over the source"

        _left, _top, right, _bottom = editor.bounding_rectangle
        x, top, bottom = origin
        line_height = bottom - top
        # Half a line of headroom above the glyphs and half a line below, for the
        # same reason as the mouse band: a band starting exactly at the text top
        # clipped every ascender on ARM64.
        start = (max(SOURCE_RECT[0] + 4, x - 8), top - line_height // 2)
        end = (min(right - 4, x + 400), second[2] - line_height // 2)
        print(f"touch drag {start} -> {end}, line height {line_height}")

        assert touch.swipe(*start, *end, steps=16), "the drag frames were refused"

        got = harness.wait_for_clipboard_text(timeout=20.0)
        assert got is not None, f"a touch drag {start} -> {end} produced no clipboard text"
        assert harness.normalise(SINGLE_WORD) in harness.normalise(got), (
            f"the touch drag returned {got!a}, which does not contain {SINGLE_WORD!r}"
        )
    finally:
        harness.wait_for_no_overlay()
        window.close(force=True)


def test_the_overlay_toolbar_answers_a_tap(overlay, touch):
    """A toolbar button, tapped rather than clicked.

    Cheap, and it separates two failures that look alike: an overlay that ignores
    touch entirely, and a canvas that handles touch while the toolbar does not.
    `TogglePattern` is read back rather than assumed, because a tap that misses
    and a toggle that did not change state print the same way.
    """
    # find_all(automation_id=...) from the overlay's root element, the same way
    # test_powerocr_toolbar_and_keyboard does it: `locator("#id")` searches by
    # *name*, so the `#id` form looks for an element literally called
    # "#SingleLineToggleButton" and finds nothing.
    root = UiaElement.from_handle(overlay.hwnd)
    found = root.find_all(automation_id=harness.SINGLE_LINE_ID)
    assert found, f"the overlay has no element with AutomationId {harness.SINGLE_LINE_ID!r}"
    target = found[0]

    before = target.toggle_state
    assert target.tap(touch=touch), "the tap on the toolbar button was refused"
    time.sleep(0.5)
    after = target.toggle_state

    assert after != before, (
        f"{harness.SINGLE_LINE_ID} reported {before} before the tap and {after} "
        "after, so the tap did not reach it"
    )
    print(f"{harness.SINGLE_LINE_ID}: {before} -> {after} by touch")
