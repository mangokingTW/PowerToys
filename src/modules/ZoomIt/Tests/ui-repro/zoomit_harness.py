"""Getting ZoomIt into a state where a hotkey can be observed at all.

Four things stand between a fresh runner and a measurable hotkey, and every one
of them produced a confident "not reproduced" while being the actual reason
nothing happened:

  * the module is off in `settings.json`, so no hotkey is registered;
  * the runner's `PowerToys.Settings` process holds those flags in memory and
    writes them back over any edit made while it is alive;
  * the runner does not start the module even with the flag set, so the
    executable is launched directly -- it is Sysinternals ZoomIt and registers
    its own hotkeys;
  * it opens its options dialog on first run and registers **nothing** until
    that dialog is dismissed. Its OK button is off-screen on a 1024x768 desktop,
    so a coordinate click misses it; the invoke pattern does not need a pointer.

Each helper here reports what it observed rather than returning a bool, because
the failure that matters is a run that measured none of the above.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

PT_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "PowerToys"
RUNNER = PT_ROOT / "PowerToys.exe"
ZOOMIT = PT_ROOT / "PowerToys.ZoomIt.exe"
SETTINGS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "PowerToys" / "settings.json"

# The Sysinternals licence gate. Unset, the tool holds a licence dialog and
# registers no hotkeys -- indistinguishable from a hotkey that never fires.
EULA_KEY = r"HKCU:\Software\Sysinternals\ZoomIt"

# German: `]` is AltGr+9 here, which is what the report is about.
GERMAN_LAYOUT = "00000407"

DIALOG_CLASS = "#32770"
ZOOM_WINDOW_CLASS = "ZoomitClass"


def powershell(script: str, timeout: float = 120.0) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return (out.stdout or "").strip()


def powertoys_processes() -> list[str]:
    listed = powershell(
        "(Get-Process -Name PowerToys* -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty Name) -join ','"
    )
    return [name for name in listed.split(",") if name]


def stop_powertoys() -> None:
    powershell("Stop-Process -Name PowerToys* -Force -ErrorAction SilentlyContinue")
    time.sleep(4.0)


def enable_zoomit() -> bool:
    """Turns the module on, with every PowerToys process down first.

    Returns what the file says afterwards. Edited while `PowerToys.Settings` is
    running, this edit is silently reverted -- that process rewrites the whole
    file from its in-memory state, and a re-read a minute later showed `False`
    again with nothing having reported an error.
    """
    stop_powertoys()
    if not SETTINGS.exists():
        return False
    data = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    data.setdefault("enabled", {})["ZoomIt"] = True
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return bool(json.loads(SETTINGS.read_text(encoding="utf-8-sig"))["enabled"]["ZoomIt"])


def accept_eula() -> int:
    powershell(
        f"New-Item -Path '{EULA_KEY}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{EULA_KEY}' -Name EulaAccepted -Value 1 -Type DWord"
    )
    value = powershell(f"(Get-ItemProperty '{EULA_KEY}').EulaAccepted")
    return int(value) if value.isdigit() else 0


def start_zoomit(timeout: float = 20.0) -> bool:
    """Starts the module directly and waits for the process.

    No `/accepteula`: this build exits immediately when given it, which read as
    "ZoomIt will not start" for a while. The registry value above is the gate.
    """
    if "PowerToys.ZoomIt" in powertoys_processes():
        return True
    if not ZOOMIT.exists():
        return False
    subprocess.Popen([str(ZOOMIT)], cwd=str(PT_ROOT))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "PowerToys.ZoomIt" in powertoys_processes():
            return True
        time.sleep(1.0)
    return False


def zoomit_windows() -> list:
    """Every visible window ZoomIt owns, by title or class."""
    from wintegrate.diagnostics import WindowCensus

    return [
        snapshot
        for snapshot in WindowCensus.capture()
        if snapshot.is_visible
        and (
            "zoomit" in (snapshot.title or "").lower()
            or "zoomit" in (snapshot.class_name or "").lower()
        )
    ]


# The options dialog, as opposed to a message box. Both are `#32770`.
OPTIONS_TITLE_MARKER = "sysinternals"

# Cancel before OK on the options dialog. OK *applies* the settings, and applying
# them includes the "Run ZoomIt when Windows starts" entry -- which on a hosted
# x64 runner fails with "Error configuring auto start: The system cannot find the
# file specified", and dismissing that error reopens the options dialog. The two
# then alternate: a 15s dismissal loop went round ten times and gave up with both
# windows still on screen. Cancel closes without applying anything, and the
# hotkeys come from the saved configuration rather than from this dialog.
CONFIRM_BUTTONS = ("cancel", "取消", "ok", "確定")
MESSAGE_BOX_BUTTONS = ("ok", "確定", "cancel", "取消")


def dismiss_zoomit_dialogs(timeout: float = 20.0) -> list[str]:
    """Closes ZoomIt's options dialog and any message box, and says which.

    Buttons are matched in English and Chinese: this also ran on a zh-TW host
    where the button is `確定`, an English-only match left the dialog up, and the
    next test then measured an unarmed ZoomIt.
    """
    from wintegrate.element import UiaElement
    from wintegrate.interop import send_keys

    closed: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dialogs = [w for w in zoomit_windows() if (w.class_name or "") == DIALOG_CLASS]
        if not dialogs:
            break
        for dialog in dialogs:
            is_options = OPTIONS_TITLE_MARKER in (dialog.title or "").lower()
            preferred = CONFIRM_BUTTONS if is_options else MESSAGE_BOX_BUTTONS
            root = UiaElement.from_handle(dialog.hwnd)
            buttons = {
                (b.name or "").strip().lower(): b
                for b in root.find_all(control_type_id=50000)  # Button
            }
            for wanted in preferred:
                if wanted in buttons:
                    # The name is read *before* invoking: read after, the dialog
                    # is already gone and it comes back empty, which is why an
                    # earlier log said a message box was closed "via ''".
                    closed.append(f"{dialog.title!r} via {wanted!r}")
                    # invoke, not click: the options dialog is taller than the
                    # desktop, so its buttons sit off-screen and a coordinate
                    # click lands nowhere.
                    buttons[wanted].invoke()
                    break
            else:
                closed.append(f"{dialog.title!r} via Escape (buttons: {sorted(buttons)})")
                send_keys("{ESC}")
        time.sleep(1.5)
    return closed


def zoomit_is_armed() -> bool:
    """True when no ZoomIt dialog is up, so its hotkeys are registered."""
    return not [w for w in zoomit_windows() if (w.class_name or "") == DIALOG_CLASS]


# Physical AltGr, as opposed to the generic Ctrl+Alt a send_keys chord produces.
# `%` maps to VK_MENU, which is neither left nor right Alt; the real key is
# VK_RMENU carrying KEYEVENTF_EXTENDEDKEY, and on an AltGr layout a left Ctrl
# accompanies it. Those are different key events and an application is free to
# tell them apart, so the reproduction measures both rather than assuming.
VK_LCONTROL = 0xA2
VK_RMENU = 0xA5


def send_real_altgr(vk: int) -> int:
    """Sends AltGr+`vk` the way the physical key does, by scan code.

    One SendInput batch, so nothing can interleave between the modifier and the
    key. Returns how many events were queued.
    """
    import ctypes

    from wintegrate.interop import INPUT, send_scan_key, user32

    events = [
        (VK_LCONTROL, False, False),
        (VK_RMENU, False, True),
        (vk, False, False),
        (vk, True, False),
        (VK_RMENU, True, True),
        (VK_LCONTROL, True, False),
    ]
    array = (INPUT * len(events))(*[send_scan_key(k, up, ext) for k, up, ext in events])
    return user32.SendInput(len(events), array, ctypes.sizeof(INPUT))


def visible_windows() -> dict:
    from wintegrate.diagnostics import WindowCensus

    return {
        s.hwnd: (s.is_visible, s.title, s.class_name, s.pid) for s in WindowCensus.capture()
    }


def windows_shown_by(action, settle: float = 2.5) -> list[str]:
    """Runs `action` and returns the windows that *became visible*.

    A visibility transition, not `WindowCensus.diff().added`: ZoomIt builds its
    overlays at startup and only shows them, so a new-handle check reports an
    empty list however well the hotkey works. That check is what made an armed,
    reacting ZoomIt look dead.
    """
    before = visible_windows()
    action()
    time.sleep(settle)
    after = visible_windows()
    return [
        f"{value[1]!r} class={value[2]!r} pid={value[3]}"
        for hwnd, value in after.items()
        if value[0] and (hwnd not in before or not before[hwnd][0])
    ]
