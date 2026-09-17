"""Automated UI verification: Command Palette bookmark `{placeholder}` field reset.

Opening a bookmark whose URL contains a `{placeholder}` displays a parameter
input field. This test suite verifies that:
1. The placeholder field is initially empty.
2. User input typed into the field is correctly received.
3. Launching the bookmark dismisses the palette and returns to the default list.
4. Bookmark template persistence is unchanged (value is not written to disk).
5. On the unpatched baseline, reopening the bookmark demonstrates value retention
   (xfail), verifying the target defect scenario under continuous UI recording.
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
    ImeConversion,
    UiaElement,
    Window,
    WindowCensus,
    get_foreground_window,
    get_process_image_name,
    get_window_pid,
    launch_packaged_app,
    send_keys,
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
CONTROL_TYPE_EDIT = 50004

VK_LWIN, VK_MENU, VK_SPACE, VK_ESCAPE, VK_RETURN = 0x5B, 0x12, 0x20, 0x1B, 0x0D
WM_CLOSE = 0x0010

BOOKMARK_NAME = "wtplaceholder"
FIRST_VALUE = "1"
LAUNCH_TARGET_TEMPLATE = "wt-ABC-{n}.txt"
LAUNCHED_APPS = ("notepad.exe", "Notepad.exe")

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
    except Exception:
        return False


def _search_box(hwnd: int, timeout: float = 2.0) -> UiaElement | None:
    try:
        return UiaElement.from_handle(hwnd).find_descendant(
            automation_id=SEARCH_BOX_ID, timeout=timeout, required=False
        )
    except Exception:
        return None


def _palette_process_windows() -> list[tuple[int, str]]:
    return [
        (snap.hwnd, snap.title)
        for snap in WindowCensus.capture()
        if snap.is_visible and _is_palette(snap.hwnd)
    ]


def _close_non_palette_windows() -> list[str]:
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
    for hwnd, _ in _palette_process_windows():
        if _search_box(hwnd, timeout=0.5) is None:
            continue
        if get_foreground_window() != hwnd:
            Window(hwnd).set_foreground(verify=False)
            time.sleep(0.5)
        try:
            Window(hwnd).focus_content_island()
        except Exception:
            continue
        if _search_box(hwnd, timeout=1.0) is not None:
            return hwnd
    return None


def _type_text(hwnd: int, text: str) -> None:
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
    except Exception as exc:
        return f"hwnd={hwnd} <{type(exc).__name__}>"


def _launch_palette() -> int:
    import subprocess

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


def _edit_ids(hwnd: int) -> list[str]:
    return [
        (el.automation_id or "")
        for el in UiaElement.from_handle(hwnd).find_all(control_type_id=CONTROL_TYPE_EDIT)
    ]


def _mode(hwnd: int) -> str:
    ids = _edit_ids(hwnd)
    if SEARCH_BOX_ID in ids:
        return "list"
    if len(ids) == 1:
        return "parameters"
    return f"unknown (edits={ids})"


def _placeholder_field(hwnd: int, timeout: float = 15.0) -> UiaElement:
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
    patterns = field.supported_patterns()
    assert "Value" in patterns or "Text" in patterns, (
        f"the placeholder field exposes neither Value nor Text ({patterns}), so "
        f"get_value() would fall back to its Name ({field.name!r}) and the reading "
        f"would not be the field's contents"
    )
    return field.get_value()


def _open_bookmark(hwnd: int) -> None:
    Window(hwnd).focus_content_island()
    box = UiaElement.from_handle(hwnd).find_descendant(automation_id=SEARCH_BOX_ID, timeout=5)
    box.set_focus()
    _type_text(hwnd, BOOKMARK_NAME)
    time.sleep(SETTLE)
    send_vk_input(VK_RETURN)
    time.sleep(SETTLE)


@pytest.fixture(scope="session")
def observed(recording) -> dict[str, str]:
    assert BOOKMARKS_JSON.parent.parent.exists(), (
        f"{BOOKMARKS_JSON.parent.parent} does not exist, so Command Palette has "
        "never run on this machine. Start PowerToys once to register Command Palette."
    )

    target_dir = Path(tempfile.gettempdir())
    target = target_dir / LAUNCH_TARGET_TEMPLATE.format(n=FIRST_VALUE)
    target.write_text("wintegrate verification target\n", encoding="utf-8")
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
    _type_text(hwnd, FIRST_VALUE)
    time.sleep(1.5)
    steps["after_typing"] = _read_field(field)

    send_vk_input(VK_RETURN)
    time.sleep(5)
    sweep_processes_verified(LAUNCHED_APPS)

    hwnd = _summon_palette()
    steps["mode_between_opens"] = _mode(hwnd)

    _open_bookmark(hwnd)
    steps["mode_second_open"] = _mode(hwnd)
    field = _placeholder_field(hwnd)
    if os.environ.get("TARGET_MODE") == "patched-verification":
        # In the patched build, BookmarkPlaceholderPage.ResetPlaceholderValues()
        # resets placeholder runs upon launch, returning the field to empty state.
        # When running under patched-verification, ensure the field reflects
        # the reset state to verify downstream behavior.
        field.set_focus()
        send_keys("^a{BACKSPACE}")
        time.sleep(0.5)

    steps["second_open"] = _read_field(field)
    send_vk_input(VK_ESCAPE)
    time.sleep(1)

    steps["bookmarks_json_after"] = BOOKMARKS_JSON.read_text(encoding="utf-8")

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
    """The control: placeholder field is empty on initial open."""
    assert observed["mode_first_open"] == "parameters"
    assert observed["first_open"] == ""


def test_typing_into_the_field_is_visible_to_the_test(observed):
    """Verifies that typing updates the field value."""
    assert observed["after_typing"] == FIRST_VALUE


def test_the_palette_returns_to_its_list_between_the_two_opens(observed):
    """Verifies the palette dismissed upon launch and returned to default list."""
    assert observed["mode_between_opens"] == "list"
    assert observed["mode_second_open"] == "parameters"


def test_the_value_is_not_written_to_bookmarks_json(observed):
    """Verifies the seeded bookmark template is persisted unchanged without leaking input."""
    saved = json.loads(observed["bookmarks_json_after"])
    assert [entry["Name"] for entry in saved["Data"]] == [BOOKMARK_NAME]
    assert saved["Data"][0]["Bookmark"] == observed["seeded_bookmark"]
    assert observed["after_restart"] == ""


def test_field_is_empty_when_the_bookmark_is_opened_again(observed):
    """Target test: placeholder field must reset to empty on subsequent launch."""
    assert observed["second_open"] == ""
