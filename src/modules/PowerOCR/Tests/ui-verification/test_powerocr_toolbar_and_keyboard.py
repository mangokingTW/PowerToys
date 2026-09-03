"""Toolbar and keyboard checks from the Text Extractor checklist, unattended.

`tests-checklist-text-extractor.md` leaves these unchecked, and
microsoft/PowerToys#49431 counts them among the 16 blocked on an attached
interactive desktop and WinAppDriver. They need neither: the toolbar's
AutomationIds carry Toggle and Invoke patterns, so UI Automation drives them
directly, and the overlay is raised by signalling PowerOCR's own named event
rather than by a hotkey that would need a focused desktop.

Each test names the checklist line it covers. The overlay is raised per test
rather than shared: one of these dismisses it on purpose, and a fixture that
leaves the next test to guess whether it is still up is how a suite ends up
green without having run anything.
"""

from __future__ import annotations

import sys
import time

import pytest
from wintegrate import UiaElement, Window, send_keys
from wintegrate.interop import SM_CXSCREEN, SM_CYSCREEN, user32

import powerocr_harness as h

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives Windows UI")


#: UIA ControlType for a list item, used to enumerate the language list.
LIST_ITEM = 50007

#: ExpandCollapseState values worth naming.
COLLAPSED, EXPANDED = 0, 1


def _by_id(overlay: Window, automation_id: str) -> UiaElement:
    """The toolbar element with this AutomationId, or a failure that names it.

    `find_all(automation_id=...)` rather than `locator(f"#{id}")`: a string handed to
    `locator` is treated as a *name*, so the `#id` form silently searches for an
    element literally called "#SingleLineToggleButton" and finds nothing. That is
    how an earlier version of these tests came to look for controls that were there
    all along.
    """
    root = UiaElement.from_handle(overlay.hwnd)
    found = root.find_all(automation_id=automation_id)
    assert found, (
        f"the overlay has no element with AutomationId {automation_id!r}; the checklist "
        f"names it, and the PR states the existing AutomationIds are preserved"
    )
    return found[0]


# --- Toolbar / flyout accessibility -----------------------------------------


def test_toolbar_buttons_have_accessible_names(overlay):
    """*Confirm that SingleLineToggleButton, TableToggleButton, SettingsButton and
    CancelButton each have a non-empty accessible name readable by Narrator.*

    Non-empty is the whole assertion, deliberately: the names are localized, and on a
    zh-TW host all four came back in Chinese, so asserting any particular string would
    be asserting the runner's display language. They are printed through ascii() for
    the same reason -- a console using a legacy code page cannot encode them, and a
    diagnostic must not be the thing that fails the test.
    """
    names = {}
    for automation_id in h.TOOLBAR_BUTTON_IDS:
        element = _by_id(overlay, automation_id)
        name = element.name or ""
        names[automation_id] = name
        assert name.strip(), f"{automation_id} has no accessible name (Name={name!r})"
        assert name.strip() != automation_id, (
            f"{automation_id}'s accessible name is just its AutomationId, which is a "
            f"developer identifier and not something to read aloud"
        )
    print("accessible names:", {k: ascii(v) for k, v in names.items()})


# --- Capture-mode toggles ---------------------------------------------------


@pytest.mark.parametrize(
    "automation_id,label",
    [(h.SINGLE_LINE_ID, "Single-line"), (h.TABLE_ID, "Table")],
)
def test_capture_mode_toggle_reports_its_state(overlay, automation_id, label):
    """*Toggle Single-line / Table mode; verify the button reports Selected = true.*

    Driven through TogglePattern rather than by clicking: a click would also be
    testing that the coordinates were right, and the state is what the checklist
    asks about. `set_toggle_verified` re-reads the state instead of assuming the
    call landed -- Toggle() only advances the state, so a control already on must
    be left alone.
    """
    element = _by_id(overlay, automation_id)
    before = element.toggle_state
    assert before is not None, (
        f"{automation_id} does not support TogglePattern, so nothing can report "
        f"whether {label} mode is on"
    )

    assert element.set_toggle_verified(True), f"{label} mode did not report itself on"
    assert element.toggle_state == 1, (
        f"{label} mode reports {element.toggle_state} after being switched on"
    )
    print(f"{label}: {before} -> {element.toggle_state}")

    # Left as it was found, so the tests do not depend on their own order.
    element.set_toggle_verified(bool(before))


# --- Keyboard reachability --------------------------------------------------


def test_every_toolbar_button_is_reachable_by_tab(overlay):
    """*Tab through toolbar controls and confirm each is reachable by keyboard
    without a mouse.*

    Records what has focus after each Tab and requires all four buttons to show
    up. Reading the focused element rather than counting keystrokes is the point:
    a Tab that goes nowhere is indistinguishable from one that works if nobody
    looks at where focus landed.
    """
    overlay.set_foreground(verify=False)
    time.sleep(0.4)

    seen: list[str] = []
    for _ in range(14):
        send_keys("{TAB}")
        time.sleep(0.25)
        try:
            focused = UiaElement.get_focused()
            seen.append(focused.automation_id or f"<{focused.control_type_name}>")
        except Exception as exc:  # noqa: BLE001 - recorded; the assertion reports it
            seen.append(f"<unreadable: {type(exc).__name__}>")

    missing = [b for b in h.TOOLBAR_BUTTON_IDS if b not in seen]
    assert not missing, (
        f"{missing} never received focus in 14 tabs, so they are not reachable "
        f"without a mouse. Focus order observed: {seen}"
    )
    print("tab order:", seen)


def test_language_list_opens_from_the_keyboard(overlay):
    """*Open the language flyout via keyboard and confirm language items are
    accessible.*

    Covered with Alt+Down, not the Shift+F10 the checklist names. Measured on the
    shipped build, each gesture against a freshly raised overlay with the combo
    focused but not clicked:

        Alt+Down    collapsed -> expanded    (items 0 -> 2)
        F4          collapsed -> expanded    (items 0 -> 2)
        Space       collapsed -> collapsed
        Shift+F10   collapsed -> collapsed
        Apps key    collapsed -> collapsed

    The language chooser here is a toolbar ComboBox, and Alt+Down / F4 are its
    keyboard affordances; Shift+F10 belongs to the right-click context menu the
    checklist line was written for. So the item's intent -- the list is reachable
    without a mouse -- holds, and its letter is out of date. Worth telling whoever
    maintains that file.

    Two things this has to be careful about, both of which made an earlier version
    of it assert nothing:

    - `set_focus()` clicks by default, which opens the combo with the mouse. Focus
      is taken with `click=False` and the collapsed state asserted first, so the
      expansion can only be attributed to the keystroke.
    - a collapsed ComboBox reports 0 items, but one opened by a click reports 2, so
      counting items is a measure of how it was opened rather than of whether it
      is open. The assertion is on ExpandCollapseState.
    """
    combo = _by_id(overlay, h.LANGUAGE_COMBO_ID)

    overlay.set_foreground(verify=False)
    time.sleep(0.3)
    combo.set_focus(verify=False, click=False)
    time.sleep(0.4)

    focused = UiaElement.get_focused().automation_id
    assert focused == h.LANGUAGE_COMBO_ID, (
        f"focus is on {focused!r}, not the language combo, so a keystroke would not reach it"
    )
    assert combo.expand_collapse_state == COLLAPSED, (
        f"the list is already in state {combo.expand_collapse_state} before any key "
        f"was sent; anything measured after this would not be the keyboard's doing"
    )

    send_keys("%{DOWN}")
    time.sleep(0.8)

    state = combo.expand_collapse_state
    assert state == EXPANDED, (
        f"Alt+Down left the language list in ExpandCollapseState {state}; the list "
        f"does not open from the keyboard"
    )

    items = combo.find_all(control_type_id=LIST_ITEM)
    assert items, "the list reports itself expanded but exposes no items"
    unnamed = [i for i in items if not (i.name or "").strip()]
    assert not unnamed, (
        f"{len(unnamed)} of {len(items)} language items have no accessible name, so a "
        f"screen reader cannot announce them"
    )
    print(f"opened by Alt+Down; {len(items)} languages:", [ascii(i.name) for i in items[:8]])


# --- Dismissal --------------------------------------------------------------


def test_escape_dismisses_the_overlay_after_toggling_modes(overlay):
    """*Press Escape after toggling toolbar modes and verify the overlay is
    dismissed.*

    The toggling is not incidental: this is the checklist item that asks whether
    the modes leave the overlay in a state Escape can still close.
    """
    for automation_id in (h.SINGLE_LINE_ID, h.TABLE_ID):
        _by_id(overlay, automation_id).set_toggle_verified(True)
        time.sleep(0.2)

    screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
    before = h.visible_overlay_windows()
    assert h.overlay_covering_primary(before, screen_w, screen_h), (
        f"the overlay is not up before Escape is sent, so this proves nothing: {before}"
    )

    overlay.set_foreground(verify=False)
    time.sleep(0.3)
    send_keys("{ESC}")

    assert h.wait_for_no_overlay(timeout=10.0), (
        f"the overlay is still visible 10s after Escape: {h.visible_overlay_windows()}"
    )
    print("dismissed by Escape after toggling both modes")
