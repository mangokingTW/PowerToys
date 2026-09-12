"""Issue #45816 — keyboard shortcuts stop working after a click inside the palette.

    https://github.com/microsoft/PowerToys/issues/45816

Open since February, labelled `Status-Reproducible`. Clicking somewhere in
Command Palette that is not a control leaves focus on the content island itself,
and from there Esc and Enter reach nothing — the report's words: *"There is no
way to get keyboard shortcuts to work again without closing and reopening
Command Palette entirely."*

**This does not propose a second test framework.** `Microsoft.CmdPal.UITests`
already drives this window and already uses the same search-box locator
(`CommandPaletteTestBase.SetSearchBoxText` finds
`By.AccessibilityId("MainSearchBox")`). This is a standalone reproduction that
happens to be written with `wintegrate`; the measurement is the point.

**No extension is needed.** The report reproduces on an extension page because
those have no search input to focus. A built-in placeholder form page does the
same job: focus starts on the form's own text box, and one click moves it off.
The reproduction seeds its own bookmark to get that page, so nothing is installed
and nothing is downloaded.

Run it with::

    pip install wintegrate pytest
    pytest src/modules/cmdpal/Tests/ui-repro -v -rxX -s

Measured on Command Palette **0.12.12365.0** (PowerToys 0.101.2362.0),
Windows 11 26100 ARM64:

    step                              on list page  cloaked  focused
    palette open                      True          False    MainSearchBox
    form page opened                  False         False    the form TextBox
    Esc, no click yet                 True          False    MainSearchBox
    form page again                   False         False    the form TextBox
    clicked the footer label          False         False    InputSiteWindowClass
    Esc after the click               False         False    InputSiteWindowClass
    Enter after the click             False         False    InputSiteWindowClass
    Esc three more times              False         False    InputSiteWindowClass
    palette closed and reopened       True          False    MainSearchBox

The third row is the control that makes the sixth mean anything: the same Esc, on
the same page, differing only in whether a click happened first.

`InputSiteWindowClass` is the WinUI content island's own container. Focus lands
there rather than on any control, which is consistent with the report's claim
that shortcuts only work while a control that handles them has focus.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest

pytest.importorskip("wintegrate", reason="pip install wintegrate")

from wintegrate import (  # noqa: E402
    ImeConversion,
    UiaElement,
    Window,
    WindowCensus,
    get_foreground_window,
    get_process_image_name,
    get_window_pid,
    launch_packaged_app,
    send_keys,
    send_mouse_click,
    send_physical_keys,
    send_vk_input,
    sweep_processes_verified,
)
from wintegrate.interop import user32  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="drives the packaged app through UI Automation"
)

PACKAGE_FAMILY = "Microsoft.CommandPalette_8wekyb3d8bbwe"
AUMID = f"{PACKAGE_FAMILY}!App"
PROCESS = "Microsoft.CmdPal.UI.exe"

SEARCH_BOX_ID = "MainSearchBox"
CONTROL_TYPE_TEXT = 50020
CONTROL_TYPE_EDIT = 50004

# Win+Alt+Space is the palette's default hotkey. send_keys() has no Win-key
# modifier in its grammar, so the chord goes through send_vk_input() by VK.
VK_LWIN, VK_MENU, VK_SPACE, VK_ESCAPE, VK_RETURN = 0x5B, 0x12, 0x20, 0x1B, 0x0D
WM_CLOSE = 0x0010

# The form page is reached through a bookmark with a {placeholder}. The name is
# ours, so matching it does not depend on the display language — the built-in
# pages are all localised, and matching a translated string is how a test passes
# on one machine and fails on another.
BOOKMARK_NAME = "wtshortcutform"

# Utilities.BaseSettingsPath("Microsoft.CmdPal") for a *packaged* process:
# SHGetKnownFolderPath with KF_FLAG_FORCE_APP_DATA_REDIRECTION resolves to the
# package's LocalState, and the folder name is not appended when packaged (see
# the `if (!IsPackaged())` in that method). Both were settled by experiment.
BOOKMARKS_JSON = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Packages"
    / PACKAGE_FAMILY
    / "LocalState"
    / "bookmarks.json"
)

LAUNCH_TIMEOUT = float(os.environ.get("CMDPAL_LAUNCH_TIMEOUT", "40"))
SETTLE = float(os.environ.get("CMDPAL_SETTLE", "2.5"))

# DWMWA_CLOAKED. Kept local rather than reached for from the library because
# wintegrate has no cloaking API yet — and the reason it needs one is this:
# IsWindowVisible answers True for this window whether the palette is on screen
# or hidden, so it cannot tell "dismissed" from "showing". Measured both ways
# before trusting either.
DWMWA_CLOAKED = 14
_dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)


def _is_cloaked(hwnd: int) -> bool | None:
    """Whether DWM is hiding the window. None when the attribute is unavailable."""
    value = wintypes.DWORD(0)
    hresult = _dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_uint(DWMWA_CLOAKED),
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
    return None if hresult != 0 else bool(value.value)


def _is_palette(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        return "cmdpal" in (get_process_image_name(get_window_pid(hwnd)) or "").lower()
    except Exception:  # noqa: BLE001 - a dead handle is just "not the palette"
        return False


def _search_box(hwnd: int, timeout: float = 2.0) -> UiaElement | None:
    try:
        return UiaElement.from_handle(hwnd).find_descendant(
            automation_id=SEARCH_BOX_ID, timeout=timeout, required=False
        )
    except Exception:  # noqa: BLE001 - a window mid-teardown raises COM errors
        return None


def _palette_process_windows() -> list[tuple[int, str]]:
    """Every visible top-level window owned by the palette's process, with its title."""
    return [
        (snap.hwnd, snap.title)
        for snap in WindowCensus.capture()
        if snap.is_visible and _is_palette(snap.hwnd)
    ]


def _close_non_palette_windows() -> list[str]:
    """Closes windows the palette's process owns that are not the palette.

    On a first run Command Palette puts up a toast — its own class, its own
    window, the same process — and on a hosted runner that toast takes and keeps
    the foreground. Identifying the palette as "the foreground window owned by
    cmdpal" therefore picked the toast, found no search box, and waited out the
    timeout; the failure said `Foreground is title='Command Palette Toast'`.

    So the palette is identified by *content* — the window that has a
    MainSearchBox — and anything else that process owns is closed rather than
    waited on. Matching the toast by title would tie this to the display
    language.
    """
    closed = []
    for hwnd, title in _palette_process_windows():
        if _search_box(hwnd, timeout=0.5) is not None:
            continue
        closed.append(title or f"hwnd={hwnd}")
        user32.SendMessageW(hwnd, WM_CLOSE, 0, 0)
    if closed:
        time.sleep(1.5)
    return closed


def _ready_palette() -> int | None:
    """The palette, identified by having a search box, and brought to the front.

    Owning the foreground is not readiness for a window that hides itself on
    focus loss: anything that steals the foreground for a moment leaves a live
    HWND whose content island has been torn down. And the foreground is not
    identity either — see `_close_non_palette_windows`.
    """
    for hwnd, _ in _palette_process_windows():
        if _search_box(hwnd, timeout=0.5) is None:
            continue
        if get_foreground_window() != hwnd:
            Window(hwnd).set_foreground(verify=False)
            time.sleep(0.5)
        try:
            Window(hwnd).focus_content_island()
        except Exception:  # noqa: BLE001
            continue
        # Re-checked after the focus change: a window that went away mid-way
        # would otherwise be returned as ready.
        if _search_box(hwnd, timeout=1.0) is not None:
            return hwnd
    return None


def _type_text(hwnd: int, text: str) -> None:
    """Types literal text as physical key presses rather than as Unicode.

    Both put the same characters in the field. The difference is what anything
    watching the keyboard sees: a physical key carries a real virtual key, while
    Unicode injection arrives as `vkCode = VK_PACKET` with the character in
    `scanCode`. A visualiser drawing the recording labels the former correctly and
    mislabels the latter — `a` (97) shows up as Numpad1, `q` (113) as F2 — so a
    recording of Unicode typing either says nothing or says something wrong.

    `ime_mode` establishes English first, because a scan code means whatever the
    active input state says it means: under Bopomofo, unshifted letters are
    phonetic keys and correct injection produces an empty field. It also normalises
    Caps Lock, which is desktop-global. The mode has to be *established*, not
    detected — `get_ime_status()` reports no IMM32 context for this window while
    `WM_IME_CONTROL` still works.

    Named keys and chords stay on `send_keys`: those already carry real virtual
    keys.
    """
    with Window(hwnd).ime_mode(ImeConversion.ALPHANUMERIC):
        send_physical_keys(text)


def _describe_foreground() -> str:
    hwnd = get_foreground_window()
    try:
        from wintegrate import get_window_class, get_window_title

        return (
            f"hwnd={hwnd} class={get_window_class(hwnd)!r} "
            f"title={get_window_title(hwnd)!r} "
            f"process={get_process_image_name(get_window_pid(hwnd))!r}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"hwnd={hwnd} <{type(exc).__name__}>"


def _launch_palette() -> int:
    """Starts the palette by AUMID. The hotkey is registered by the palette's own
    process, so straight after a sweep there is nothing listening for it."""
    subprocess.Popen(launch_packaged_app(AUMID), close_fds=True)
    deadline = time.monotonic() + LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(2)
        hwnd = _ready_palette()
        if hwnd:
            return hwnd
        closed = _close_non_palette_windows()
        if closed:
            print(f"closed windows that are not the palette: {closed}")
    raise AssertionError(
        f"Command Palette did not become ready within {LAUNCH_TIMEOUT}s. "
        f"Foreground is {_describe_foreground()}. "
        f"Windows owned by that process: {_palette_process_windows()}."
    )


def _summon_palette() -> int:
    """Re-opens an already-running palette with Win+Alt+Space."""
    deadline = time.monotonic() + LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        send_vk_input(VK_SPACE, (VK_LWIN, VK_MENU))
        time.sleep(SETTLE)
        hwnd = _ready_palette()
        if hwnd:
            return hwnd
        _close_non_palette_windows()
        time.sleep(1)
    raise AssertionError(
        f"Win+Alt+Space did not summon a ready palette within {LAUNCH_TIMEOUT}s. "
        f"Foreground is {_describe_foreground()}. "
        f"Windows owned by that process: {_palette_process_windows()}."
    )


def _focused_identity() -> str:
    """Which element has focus, by automation id or class — not by name.

    The names here are localised; `InputSiteWindowClass` and `MainSearchBox` are
    not, which is what makes this assertable on a runner in any language.
    """
    try:
        element = UiaElement.get_focused()
        return element.automation_id or element.class_name or f"ct{element.control_type_id}"
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}>"


def _snapshot(hwnd: int) -> dict[str, object]:
    """Everything the assertions need, read together so a step is one row."""
    return {
        "on_list_page": _search_box(hwnd, timeout=1.5) is not None,
        "cloaked": _is_cloaked(hwnd),
        "focus": _focused_identity(),
    }


def _footer_label(hwnd: int) -> tuple[str, tuple[int, int, int, int]] | None:
    """The bottom-most Text element that has a rectangle.

    Located by geometry, not by name: the footer reads "Open" in English and
    something else everywhere. A Text element is also inert — an earlier version
    of this clicked a label that belonged to a command button and navigated
    somewhere instead of stranding focus, which measured nothing.
    """
    lowest = None
    for element in UiaElement.from_handle(hwnd).find_all(control_type_id=CONTROL_TYPE_TEXT):
        left, top, right, bottom = element.bounding_rectangle
        if right > left and bottom > top and (lowest is None or top > lowest[1][1]):
            lowest = (element.name or "", (left, top, right, bottom))
    return lowest


def _open_form_page(hwnd: int) -> bool:
    """Types the bookmark's name and opens it. True when the form page is up."""
    box = _search_box(hwnd, timeout=8.0)
    if box is None:
        return False
    box.set_focus()
    send_keys("^a")
    _type_text(hwnd, BOOKMARK_NAME)
    time.sleep(SETTLE)
    send_vk_input(VK_RETURN)
    time.sleep(3.0)
    return _search_box(hwnd, timeout=1.5) is None


def _click_footer(hwnd: int) -> str:
    label = _footer_label(hwnd)
    assert label is not None, "no Text element with a rectangle to click"
    name, (left, top, right, bottom) = label
    send_mouse_click((left + right) // 2, (top + bottom) // 2)
    time.sleep(2.0)
    return name


@pytest.fixture(scope="session")
def observed(recording) -> dict[str, dict[str, object]]:
    """Drives the sequence once and returns a snapshot per step.

    One pass, several assertions: the interaction is inherently ordered — "Esc
    after the click" only means something if the click really moved focus — and
    re-driving it per test would be slower and prove less.
    """
    assert BOOKMARKS_JSON.parent.parent.exists(), (
        f"{BOOKMARKS_JSON.parent.parent} does not exist, so Command Palette has "
        "never run on this machine. Start PowerToys once: the Command Palette "
        "MSIX is registered on first run, not by the installer."
    )

    target = Path(tempfile.gettempdir()) / "wt-45816-{n}.txt"
    (Path(tempfile.gettempdir()) / "wt-45816-1.txt").write_text("target\n", encoding="utf-8")

    sweep_processes_verified([PROCESS], package_family_name=PACKAGE_FAMILY)
    BOOKMARKS_JSON.parent.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_JSON.write_text(
        json.dumps(
            {
                "Data": [
                    {
                        "Id": str(uuid.uuid4()),
                        "Name": BOOKMARK_NAME,
                        "Bookmark": str(target),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    print(f"seeded {BOOKMARKS_JSON}")

    steps: dict[str, dict[str, object]] = {}
    hwnd = _launch_palette()
    recording.begin()
    steps["palette_open"] = _snapshot(hwnd)

    # Control: Esc on the form page with no click first.
    assert _open_form_page(hwnd), "the bookmark's form page did not open"
    steps["form_page"] = _snapshot(hwnd)
    send_vk_input(VK_ESCAPE)
    time.sleep(SETTLE)
    steps["esc_without_click"] = _snapshot(hwnd)

    # The same page again, this time with a click in between.
    hwnd = _ready_palette() or _summon_palette()
    assert _open_form_page(hwnd), "the form page did not open the second time"
    steps["form_page_again"] = _snapshot(hwnd)
    clicked = _click_footer(hwnd)
    print(f"clicked the footer label {clicked!r}")
    steps["after_click"] = _snapshot(hwnd)

    send_vk_input(VK_ESCAPE)
    time.sleep(SETTLE)
    steps["esc_after_click"] = _snapshot(hwnd)

    send_vk_input(VK_RETURN)
    time.sleep(SETTLE)
    steps["enter_after_click"] = _snapshot(hwnd)

    for _ in range(3):
        send_vk_input(VK_ESCAPE)
        time.sleep(1.2)
    steps["esc_three_more_times"] = _snapshot(hwnd)

    # The report says only closing and reopening recovers. Checked, because
    # "the palette is broken forever" and "it recovers on its own" are different
    # bugs and the difference is the user-visible part.
    sweep_processes_verified([PROCESS], package_family_name=PACKAGE_FAMILY)
    hwnd = _launch_palette()
    steps["after_reopen"] = _snapshot(hwnd)

    width = max(len(k) for k in steps)
    print(f"\n{'step':<{width}}  on_list  cloaked  focus")
    for key, value in steps.items():
        print(
            f"{key:<{width}}  {str(value['on_list_page']):<7}  "
            f"{str(value['cloaked']):<7}  {value['focus']}"
        )

    yield steps

    sweep_processes_verified([PROCESS], package_family_name=PACKAGE_FAMILY)


def test_the_palette_opens_on_its_list_page(observed):
    """The baseline. Everything below compares against this."""
    assert observed["palette_open"]["on_list_page"] is True
    assert observed["palette_open"]["cloaked"] is False
    assert observed["palette_open"]["focus"] == SEARCH_BOX_ID


def test_the_form_page_has_no_search_box(observed):
    """The precondition the report describes: a page with no search input.

    Focus is the form's own text box, not `MainSearchBox` — that is what makes
    this page stand in for an extension page without installing one.
    """
    for step in ("form_page", "form_page_again"):
        assert observed[step]["on_list_page"] is False, step
        assert observed[step]["focus"] != SEARCH_BOX_ID, step


def test_esc_returns_to_the_list_page_without_a_click(observed):
    """The control that makes the reproduction mean anything.

    Same page, same key. The only difference from the failing case below is
    whether a click happened first.
    """
    assert observed["esc_without_click"]["on_list_page"] is True
    assert observed["esc_without_click"]["focus"] == SEARCH_BOX_ID


def test_the_click_strands_focus_on_the_content_island(observed):
    """Proves the click did what the report describes, rather than nothing.

    Without this, "Esc did not work" could just as well mean the click missed.
    `InputSiteWindowClass` is the WinUI island's own container — not a control,
    so nothing there handles a key.
    """
    assert observed["after_click"]["focus"] == "InputSiteWindowClass"
    assert observed["after_click"]["cloaked"] is False, "the palette is still on screen"


def test_reopening_the_palette_restores_it(observed):
    """The report's recovery claim, and the proof the palette was not simply broken.

    Also rules out the reading that the process had died: it comes back on its
    list page with the search box focused.
    """
    assert observed["esc_three_more_times"]["on_list_page"] is False
    assert observed["after_reopen"]["on_list_page"] is True
    assert observed["after_reopen"]["focus"] == SEARCH_BOX_ID


@pytest.mark.xfail(
    strict=True,
    reason="issue #45816: Esc reaches nothing once a click has moved focus off the input",
)
def test_esc_still_returns_to_the_list_page_after_a_click(observed):
    """The reproduction. Asserts the wanted behaviour, so it currently fails.

    Green run = the issue still reproduces. An XPASS turns the run red, which is
    the signal this file has done its job and can go.
    """
    assert observed["esc_after_click"]["on_list_page"] is True


@pytest.mark.xfail(
    strict=True,
    reason="issue #45816: Enter reaches nothing either, so the page cannot be actioned",
)
def test_enter_still_does_something_after_a_click(observed):
    """Enter is the other shortcut the report names.

    The palette should have left this page — by launching the bookmark, which
    dismisses it. Staying on the form page with focus unchanged is the failure.
    """
    after = observed["enter_after_click"]
    assert after["on_list_page"] is True or after["cloaked"] is True
