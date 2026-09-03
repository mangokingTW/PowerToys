"""microsoft/PowerToys#50125: AltGr+9 on a German layout is eaten by ZoomIt.

    Typing `]` (AltGr+9) with ZoomIt enabled triggers DemoMirror instead of
    producing the character.

Windows delivers AltGr as **Ctrl+Alt**, and ZoomIt's Mirror Toggle is
`Ctrl+9`, with Shift and Alt selecting variants of it -- the dialog says so
itself: "enter it with the Shift key to select a region to mirror, or with the
Alt key to mirror the window under the cursor". So Ctrl+Alt+9 is not a near
miss, it is the documented window-mirror chord, and the character is lost.

That mechanism is measured here, not assumed. The issue guesses at a `)`-versus-
`9` character comparison; `test_the_mirror_hotkey_is_keyed_to_the_digit` shows
Ctrl+Shift+9 opening the *region* selector, which is the variant behaviour of a
digit-keyed hotkey rather than a match on `)`.

Three tests, and the two controls are the point of the file:

  * a control on the same class of chord one key over -- AltGr+8 must produce
    `[`, or a missing `]` means only that injection never arrived;
  * a positive control that ZoomIt's hotkeys are armed at all -- Ctrl+1 must
    raise the zoom overlay. Every early run of this reproduction reported "not
    reproduced" from a host where ZoomIt's own options dialog was up and no
    hotkey was registered;
  * the reproduction itself, as a strict xfail. It asserts the *correct*
    behaviour, so it xfails while the bug is present and turns into a hard
    failure the day a build fixes it -- a reproduction that quietly keeps
    passing after the bug is gone is worse than no reproduction.

A single display is required, and hosted runners have one. With no second
monitor ZoomIt answers the stolen keystroke with "Screen mirroring requires a
second monitor", which is a clearer witness than a mirror actually opening.
"""

from __future__ import annotations

import time

import pytest
import zoomit_harness as harness
from wintegrate import Window
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import send_keys

# One key apart on a German layout, both AltGr chords: `[` is AltGr+8, `]` is
# AltGr+9. Only the second one collides with the hotkey.
CONTROL_CHORD, CONTROL_CHARACTER = "^%8", "["
BRACKET_CHORD, BRACKET_CHARACTER = "^%9", "]"

MIRROR_REGION_CHORD = "^+9"  # Ctrl+Shift+9: the region variant of Mirror Toggle
ZOOM_CHORD = "^1"  # Ctrl+1: ZoomIt's zoom, used only to prove hotkeys are live


@pytest.fixture(scope="session")
def armed_zoomit():
    """ZoomIt running with its hotkeys actually registered.

    Everything here is asserted rather than attempted. The interesting failure
    mode of this reproduction is not a wrong answer, it is a run that never put
    ZoomIt in a state where the question could be asked -- see the harness for
    the four separate ways that happened.
    """
    assert harness.ZOOMIT.exists(), f"PowerToys.ZoomIt.exe is not at {harness.ZOOMIT}"

    enabled = harness.enable_zoomit()
    print(f"enabled.ZoomIt on disk: {enabled}")
    eula = harness.accept_eula()
    print(f"{harness.EULA_KEY}\\EulaAccepted = {eula}")
    assert eula == 1, "the Sysinternals licence gate is not set, so no hotkey is registered"

    assert harness.start_zoomit(), (
        f"PowerToys.ZoomIt did not start; running: {harness.powertoys_processes()}"
    )
    closed = harness.dismiss_zoomit_dialogs()
    print(f"dialogs dismissed: {closed}")
    assert harness.zoomit_is_armed(), (
        "a ZoomIt dialog is still up, so its hotkeys are not registered and nothing "
        f"measured here would mean anything; visible: {harness.zoomit_windows()}"
    )
    print(f"PowerToys processes: {harness.powertoys_processes()}")
    yield
    harness.dismiss_zoomit_dialogs()


@pytest.fixture
def german_editor(armed_zoomit):
    """An empty Notepad on a German layout, as (window, editor).

    Function-scoped: the reproduction leaves a ZoomIt message box owning the
    foreground, and a shared Notepad would hand the next test a window it has to
    guess the state of.
    """
    sweep_processes_verified(("notepad.exe",), ("Notepad",))
    process, window = Window.launch_and_discover(["notepad.exe"], timeout=90.0)
    try:
        window.foreground(verify=False)
        time.sleep(1.0)
        active = window.set_keyboard_layout_verified(harness.GERMAN_LAYOUT, timeout=10.0)
        # The low word is the language id; the high word is the layout handle, so
        # comparing the whole value against 0x0407 fails on a correct layout.
        assert (active & 0xFFFF) == 0x0407, (
            f"the active layout is 0x{active:08X}, not German. AltGr+9 is only `]` on a "
            f"German layout, so nothing below would be testing the reported chord."
        )
        print(f"keyboard layout: 0x{active:08X}")

        editor = window.find_text_input(timeout=60.0)
        # Ctrl+A then Delete does not clear Win11 Notepad; SetValue does.
        editor.set_value_verified("")
        editor.set_focus(click=False)
        time.sleep(0.4)
        yield window, editor
    finally:
        # Escape first: a zoom or region overlay left up is full-screen and
        # topmost, and the next test's Notepad would launch underneath it and
        # never receive a keystroke.
        send_keys("{ESC}")
        time.sleep(0.5)
        harness.dismiss_zoomit_dialogs()
        try:
            window.close(force=True)
        except Exception:
            pass
        sweep_processes_verified(("notepad.exe",), ("Notepad",))
        process.poll()


def _type_chord(editor, chord: str) -> tuple[str, list[str]]:
    """Sends `chord` and returns (text in the editor, windows that appeared)."""
    shown = harness.windows_shown_by(lambda: send_keys(chord))
    return editor.get_value() or "", shown


def test_an_altgr_chord_one_key_over_produces_its_bracket(german_editor):
    """The control: AltGr+8 must type `[` with ZoomIt running.

    Without this, a missing `]` in the reproduction is unreadable -- it could
    just as easily be an injection that never reached the editor. AltGr+9 on a
    US layout was tried as the control first and is not one: that chord produces
    no bracket there even when everything works.
    """
    _window, editor = german_editor
    text, shown = _type_chord(editor, CONTROL_CHORD)
    print(f"AltGr+8 -> {text!r}; windows shown: {shown}")
    assert CONTROL_CHARACTER in text, (
        f"AltGr+8 produced {text!r} rather than {CONTROL_CHARACTER!r}, so injection is not "
        f"reaching the editor on this host and no conclusion about AltGr+9 is available"
    )
    assert not shown, f"AltGr+8 should not raise anything, but it raised {shown}"


def test_the_mirror_hotkey_is_keyed_to_the_digit(german_editor):
    """The positive control, and the mechanism.

    Ctrl+1 raising ZoomIt's zoom overlay proves the hotkeys are armed -- the
    thing every early run of this reproduction failed to establish before
    concluding.

    Ctrl+Shift+9 then raises the mirror's *region* selector rather than mirroring
    the whole screen, which is what a digit-keyed hotkey with Shift as a modifier
    does. A hotkey matching the character `)` would have no variant to select.
    """
    _window, _editor = german_editor

    zoom = harness.windows_shown_by(lambda: send_keys(ZOOM_CHORD))
    print(f"Ctrl+1 -> {zoom}")
    assert any(harness.ZOOM_WINDOW_CLASS.lower() in w.lower() for w in zoom), (
        f"Ctrl+1 raised {zoom}, with no {harness.ZOOM_WINDOW_CLASS} among them: ZoomIt's "
        f"hotkeys are not armed on this host, so the reproduction below is not measurable"
    )
    send_keys("{ESC}")
    time.sleep(1.0)

    region = harness.windows_shown_by(lambda: send_keys(MIRROR_REGION_CHORD))
    print(f"Ctrl+Shift+9 -> {region}")
    assert region, (
        "Ctrl+Shift+9 raised nothing. The Mirror Toggle hotkey is configurable, so this "
        "run cannot show which chord it is bound to."
    )
    send_keys("{ESC}")
    time.sleep(1.0)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "microsoft/PowerToys#50125: ZoomIt claims Ctrl+Alt+9, which is how Windows "
        "delivers AltGr+9, so the `]` never reaches the focused window. Strict, so "
        "this fails loudly once a build fixes it rather than passing unnoticed."
    ),
)
def test_altgr_9_types_a_bracket_rather_than_reaching_zoomit(german_editor):
    """The reproduction, written as the behaviour a user expects.

    Two independent assertions, and either one failing is the bug:

      * the character reaches the editor -- the user's actual loss;
      * nothing of ZoomIt's appears -- on a single-display host that window is
        the message box reading "Screen mirroring requires a second monitor",
        which names the culprit without any inference.
    """
    _window, editor = german_editor
    text, shown = _type_chord(editor, BRACKET_CHORD)
    print(f"AltGr+9 -> {text!r}; windows shown: {shown}")

    assert not shown, f"AltGr+9 reached ZoomIt: it raised {shown}"
    assert BRACKET_CHARACTER in text, (
        f"AltGr+9 produced {text!r} rather than {BRACKET_CHARACTER!r}: the keystroke was "
        f"consumed by ZoomIt's Mirror Toggle instead of being typed"
    )
