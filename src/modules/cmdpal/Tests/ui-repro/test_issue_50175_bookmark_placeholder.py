"""Issue #50175 — a bookmark's `{placeholder}` field keeps the last value entered.

    https://github.com/microsoft/PowerToys/issues/50175

Open, labelled `Issue-Bug` / `Product-Command Palette`. Opening a bookmark whose
URL contains a `{placeholder}` shows the field already filled in with whatever
was typed the previous time, so the value has to be cleared by hand on every
use.

**This does not propose a second test framework.** `Microsoft.CmdPal.UITests`
already drives this window, and it already uses the same locator this file does
(`CommandPaletteTestBase.SetSearchBoxText` finds
`By.AccessibilityId("MainSearchBox")`). This is a standalone reproduction that
happens to be written with `wintegrate`; the measurement is the point and
porting it to the existing harness is straightforward.

Run it with::

    pip install wintegrate pytest
    pytest src/modules/cmdpal/Tests/ui-repro -v -rxX -s

Measured on Command Palette **0.12.12365.0** (PowerToys 0.101.2362.0),
Windows 11 26100 ARM64:

    step                                             placeholder field
    open the bookmark for the first time             ''
    type "1"                                         '1'
    launch, then open the same bookmark again        '1'   <-- expected ''
    restart Command Palette, open it again           ''

The last row is the interesting one: the value is **not** written to
`bookmarks.json` and does not survive a restart, which matches the root cause
traced on the issue — `BookmarkPlaceholderPage` and its `StringParameterRun` are
built once in `BookmarkListItem`'s constructor and cached for the life of the
session, and nothing resets them after a launch.

One locator note, offered as feedback rather than as part of the bug: the
placeholder `TextBox` comes from `StringParamTemplate` in `SearchBar.xaml`, which
sets no `x:Name` and no `AutomationProperties.AutomationId`. It is therefore
unaddressable by id, and this file has to identify it as "the only Edit in the
window that is not `MainSearchBox`" — which works because the `SwitchPresenter`
swaps `MainSearchBox` out in `Parameters` mode. Giving that `TextBox` an
automation id would let `Microsoft.CmdPal.UITests` reach it directly.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("wintegrate", reason="pip install wintegrate")

from wintegrate import (  # noqa: E402
    UiaElement,
    Window,
    get_foreground_window,
    get_process_image_name,
    get_window_pid,
    launch_packaged_app,
    send_keys,
    send_vk_input,
    sweep_processes_verified,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="drives the packaged app through UI Automation"
)

PACKAGE_FAMILY = "Microsoft.CommandPalette_8wekyb3d8bbwe"
AUMID = f"{PACKAGE_FAMILY}!App"
PROCESS = "Microsoft.CmdPal.UI.exe"

SEARCH_BOX_ID = "MainSearchBox"
CONTROL_TYPE_EDIT = 50004

# Win+Alt+Space is Command Palette's default hotkey. It cannot be written as a
# send_keys() spec: that grammar has prefixes for Ctrl, Shift and Alt but none
# for the Win key, so the modifier goes through send_vk_input() by VK instead.
VK_LWIN, VK_MENU, VK_SPACE, VK_ESCAPE, VK_RETURN = 0x5B, 0x12, 0x20, 0x1B, 0x0D

BOOKMARK_NAME = "wtplaceholder"
FIRST_VALUE = "1"

# The bookmark points at a local .txt rather than an https URL, which is what the
# issue's own repro uses. An http bookmark launches the default browser, and on a
# runner with no default browser Windows puts up a "How do you want to open
# this?" modal instead — `CommandPaletteTestBase.FindDefaultAppDialogAndClickButton`
# exists because that really happens. A .txt has a handler on every Windows, so
# the launch is predictable and nothing in this test touches the network. The
# retention is identical either way; it does not depend on what gets launched.
LAUNCH_TARGET_TEMPLATE = "wt-ABC-{n}.txt"

# Whatever ends up handling the .txt. Windows 11 ships the packaged Notepad, and
# older images the inbox one; both are swept so a leftover window cannot sit on
# the foreground for the second half of the run.
LAUNCHED_APPS = ("notepad.exe", "Notepad.exe")

# Utilities.BaseSettingsPath("Microsoft.CmdPal"), resolved for a *packaged*
# process. Two things about it are easy to get wrong, and both were settled by
# experiment rather than by reading the call:
#
#   * SHGetKnownFolderPath with KF_FLAG_FORCE_APP_DATA_REDIRECTION returns the
#     package's LocalState, not LocalCache\Local. Seeding both and giving them
#     different bookmark names showed only the LocalState one in the palette.
#   * the "Microsoft.CmdPal" folder name is *not* appended when packaged — see
#     the `if (!IsPackaged())` in Utilities.BaseSettingsPath — so the file sits
#     directly in LocalState.
BOOKMARKS_JSON = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Packages"
    / PACKAGE_FAMILY
    / "LocalState"
    / "bookmarks.json"
)

LAUNCH_TIMEOUT = float(os.environ.get("CMDPAL_LAUNCH_TIMEOUT", "40"))
SETTLE = float(os.environ.get("CMDPAL_SETTLE", "2.5"))


def _is_palette(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        return "cmdpal" in (get_process_image_name(get_window_pid(hwnd)) or "").lower()
    except Exception:  # noqa: BLE001 - a dead handle is just "not the palette"
        return False


def _ready_palette() -> int | None:
    """The palette's window, but only once its search box is actually in the tree.

    "Owns the foreground" is not the same as "is ready", and for this window it is
    not even close: the palette **hides itself when it loses focus**, so anything
    that steals the foreground for a moment leaves a live HWND whose content
    island has been torn down. Waiting on the foreground and then looking for
    `MainSearchBox` produces `ElementNotFoundError` and blames the locator.

    So readiness is the search box existing. Returns None rather than raising:
    the callers retry, because a transient thief should cost a retry and not the
    run.
    """
    hwnd = get_foreground_window()
    if not _is_palette(hwnd):
        return None
    try:
        Window(hwnd).focus_content_island()
        box = UiaElement.from_handle(hwnd).find_descendant(
            automation_id=SEARCH_BOX_ID, timeout=1.0, required=False
        )
    except Exception:  # noqa: BLE001 - a window mid-teardown raises COM errors
        return None
    return hwnd if box is not None else None


def _describe_foreground() -> str:
    """For the failure message: what was in front instead."""
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
    """Starts Command Palette by AUMID and returns it once its search box is up.

    The hotkey cannot be used here: it is registered by the Command Palette
    process, so immediately after a sweep there is nothing listening for it.
    """
    import subprocess

    subprocess.Popen(launch_packaged_app(AUMID), close_fds=True)
    deadline = time.monotonic() + LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(2)
        hwnd = _ready_palette()
        if hwnd:
            return hwnd
    raise AssertionError(
        f"Command Palette did not become ready within {LAUNCH_TIMEOUT}s. "
        f"Foreground is {_describe_foreground()}. If that is something else, it "
        f"stole the foreground and the palette hid itself."
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
        time.sleep(1)
    raise AssertionError(
        f"Win+Alt+Space did not summon a ready palette within {LAUNCH_TIMEOUT}s. "
        f"Foreground is {_describe_foreground()}."
    )


def _edit_ids(hwnd: int) -> list[str]:
    """The automation ids of every Edit in the window, in tree order."""
    return [
        (el.automation_id or "")
        for el in UiaElement.from_handle(hwnd).find_all(control_type_id=CONTROL_TYPE_EDIT)
    ]


def _mode(hwnd: int) -> str:
    """Which case of SearchBar.xaml's SwitchPresenter is showing.

    `MainSearchBox` present means the palette is back at its list; a single
    nameless Edit means the parameters bar has replaced it. This is what makes
    "the bookmark was opened again" a checkable claim rather than an assumption:
    without it, reading a form that never closed would look identical to reading
    a freshly opened one.
    """
    ids = _edit_ids(hwnd)
    if SEARCH_BOX_ID in ids:
        return "list"
    if len(ids) == 1:
        return "parameters"
    return f"unknown (edits={ids})"


def _placeholder_field(hwnd: int, timeout: float = 15.0) -> UiaElement:
    """The parameters bar's TextBox: the only Edit that is not MainSearchBox."""
    deadline = time.monotonic() + timeout
    seen: list[str] = []
    while time.monotonic() < deadline:
        edits = [
            el
            for el in UiaElement.from_handle(hwnd).find_all(control_type_id=CONTROL_TYPE_EDIT)
            if (el.automation_id or "") != SEARCH_BOX_ID
        ]
        if len(edits) == 1:
            return edits[0]
        seen = _edit_ids(hwnd)
        time.sleep(0.5)
    raise AssertionError(
        f"no single placeholder Edit in the palette after {timeout}s (edits={seen})"
    )


def _read_field(field: UiaElement) -> str:
    """Reads the field, refusing to report a value that did not come from a pattern.

    `get_value()` falls back to the element's Name when neither TextPattern nor
    ValuePattern answers — and this element's Name is the placeholder key (`n`
    for `{n}`). Without this guard an empty field could be read as `'n'` and a
    test asserting "not empty" would pass on nothing at all.
    """
    patterns = field.supported_patterns()
    assert "Value" in patterns or "Text" in patterns, (
        f"the placeholder field exposes neither Value nor Text ({patterns}), so "
        f"get_value() would fall back to its Name ({field.name!r}) and the reading "
        f"would not be the field's contents"
    )
    return field.get_value()


def _open_bookmark(hwnd: int) -> None:
    """Types the bookmark's name into the palette and opens it.

    `_ready_palette` has already established that the search box is there, so a
    miss here is a real failure rather than something to wait longer for.
    """
    Window(hwnd).focus_content_island()
    box = UiaElement.from_handle(hwnd).find_descendant(automation_id=SEARCH_BOX_ID, timeout=5)
    box.set_focus()
    send_keys(BOOKMARK_NAME)
    time.sleep(SETTLE)
    send_vk_input(VK_RETURN)
    time.sleep(SETTLE)


@pytest.fixture(scope="session")
def observed(recording) -> dict[str, str]:
    """Drives the sequence once and returns what was on screen at each step.

    One pass, several assertions: the interaction takes a couple of minutes and
    is inherently ordered — step three only means anything if step two really
    happened — so re-driving it per test would be both slow and weaker.
    """
    assert BOOKMARKS_JSON.parent.parent.exists(), (
        f"{BOOKMARKS_JSON.parent.parent} does not exist, so Command Palette has "
        "never run on this machine. Start PowerToys once: the Command Palette "
        "MSIX is registered on first run, not by the installer."
    )

    target_dir = Path(tempfile.gettempdir())
    target = target_dir / LAUNCH_TARGET_TEMPLATE.format(n=FIRST_VALUE)
    target.write_text("wintegrate reproduction target\n", encoding="utf-8")
    bookmark_url = str(target_dir / LAUNCH_TARGET_TEMPLATE.format(n="{n}"))

    sweep_processes_verified([PROCESS, *LAUNCHED_APPS], package_family_name=PACKAGE_FAMILY)
    BOOKMARKS_JSON.parent.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_JSON.write_text(
        json.dumps(
            {"Data": [{"Id": str(uuid.uuid4()), "Name": BOOKMARK_NAME, "Bookmark": bookmark_url}]}
        ),
        encoding="utf-8",
    )
    print(f"seeded {BOOKMARKS_JSON}")
    print(f"bookmark {BOOKMARK_NAME!r} -> {bookmark_url!r}")

    steps: dict[str, str] = {"seeded_bookmark": bookmark_url}
    hwnd = _launch_palette()
    recording.begin()

    _open_bookmark(hwnd)
    steps["mode_first_open"] = _mode(hwnd)
    field = _placeholder_field(hwnd)
    steps["first_open"] = _read_field(field)

    field.set_focus()
    send_keys(FIRST_VALUE)
    time.sleep(1.5)
    steps["after_typing"] = _read_field(field)

    send_vk_input(VK_RETURN)
    time.sleep(5)
    sweep_processes_verified(LAUNCHED_APPS)

    hwnd = _summon_palette()
    steps["mode_between_opens"] = _mode(hwnd)

    _open_bookmark(hwnd)
    steps["mode_second_open"] = _mode(hwnd)
    steps["second_open"] = _read_field(_placeholder_field(hwnd))
    send_vk_input(VK_ESCAPE)
    time.sleep(1)

    steps["bookmarks_json_after"] = BOOKMARKS_JSON.read_text(encoding="utf-8")

    # Restarting is what separates "cached for the session" from "saved as the
    # bookmark's default". Only the first is what the issue describes.
    sweep_processes_verified([PROCESS], package_family_name=PACKAGE_FAMILY)
    hwnd = _launch_palette()
    _open_bookmark(hwnd)
    steps["after_restart"] = _read_field(_placeholder_field(hwnd))
    send_vk_input(VK_ESCAPE)

    width = max(len(k) for k in steps)
    print("\nplaceholder field at each step:")
    for key, value in steps.items():
        print(f"  {key:<{width}}  {value!r}")

    yield steps

    sweep_processes_verified([PROCESS, *LAUNCHED_APPS], package_family_name=PACKAGE_FAMILY)
    target.unlink(missing_ok=True)


def test_field_is_empty_the_first_time_the_bookmark_is_opened(observed):
    """The control. If this fails the measurement below means nothing."""
    assert observed["mode_first_open"] == "parameters"
    assert observed["first_open"] == ""


def test_typing_into_the_field_is_visible_to_the_test(observed):
    """The control's control.

    A reading that always came back empty would make the test above pass without
    measuring anything. This is the one assertion that proves the field is being
    read rather than guessed at.
    """
    assert observed["after_typing"] == FIRST_VALUE


def test_the_palette_returns_to_its_list_between_the_two_opens(observed):
    """So the second open is a second open, and not the first form still up."""
    assert observed["mode_between_opens"] == "list"
    assert observed["mode_second_open"] == "parameters"


def test_the_value_is_not_written_to_bookmarks_json(observed):
    """Corroborates the root cause: nothing is persisted, so it is session state.

    The claim is "the file is byte-for-byte what was seeded", so it is checked by
    comparing against the seeded value. An earlier version asserted that
    `FIRST_VALUE` was not a *substring* of the saved bookmark, which failed on
    both runners for a reason that had nothing to do with the bug: a hosted
    runner's temp path is `C:\\Users\\RUNNER~1\\...`, and the 8.3 short name
    contains a literal `1`.
    """
    saved = json.loads(observed["bookmarks_json_after"])
    assert [entry["Name"] for entry in saved["Data"]] == [BOOKMARK_NAME]
    assert saved["Data"][0]["Bookmark"] == observed["seeded_bookmark"]
    assert observed["after_restart"] == ""


@pytest.mark.xfail(
    strict=True,
    reason="issue #50175: the placeholder field is pre-filled with the last value entered",
)
def test_field_is_empty_when_the_bookmark_is_opened_again(observed):
    """The reproduction. Asserts the *wanted* behaviour, so it currently fails.

    Green run = the issue still reproduces. An XPASS turns the run red, which is
    the signal that this file has served its purpose and can go.
    """
    assert observed["second_open"] == ""
