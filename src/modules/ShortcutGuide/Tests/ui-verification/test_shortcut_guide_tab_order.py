"""Automated UI verification and regression test suite for Shortcut Guide.

Includes control tests verifying original behaviors (search box, close button,
navigation rail, settings button, search input, and dismissal) that pass both
before and after patch, alongside the target tab order test verifying shortcut
card accessibility.
"""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from wintegrate import Window, UiaElement
from wintegrate.apps import sweep_processes_verified
from wintegrate.session import Session, SessionConfig
from wintegrate.interop import send_keys

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

TRIGGER_EVENT = 'Local\\ShortcutGuide-TriggerEvent-d4275ad3-2531-4d19-9252-c0becbd9b496'
WINKEY_EVENT = 'Local\\ShortcutGuide-WinKeyHoldEvent-b5eb7614-d1c4-49d7-9813-ab277aabdd80'
EXIT_EVENT = 'Local\\ShortcutGuide-ExitEvent-35697cdd-a3d2-47d6-a246-34efcc73eac0'


def settled(
    read: Callable[[], Any],
    matches: Callable[[Any], bool],
    timeout: float = 3.0,
    poll_interval: float = 0.05,
) -> Any:
    """Polls read() until matches(value), returning the last value either way."""
    deadline = time.monotonic() + timeout
    value = read()
    while not matches(value) and time.monotonic() < deadline:
        time.sleep(poll_interval)
        value = read()
    return value


def find_shortcut_guide_exe():
    env_override = os.environ.get('SHORTCUT_GUIDE_EXE')
    if env_override and os.path.isfile(env_override):
        return env_override
    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\PowerToys\WinUI3Apps\PowerToys.ShortcutGuide.exe'),
        r'C:\PowerToys\WinUI3Apps\PowerToys.ShortcutGuide.exe',
        os.path.expandvars(r'%ProgramFiles%\PowerToys\WinUI3Apps\PowerToys.ShortcutGuide.exe'),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return 'PowerToys.ShortcutGuide.exe'


@contextmanager
def shortcut_guide_session(test_name: str | None = None):
    """Context manager for running a test inside a wintegrate Session.
    
    Wrapping the test body inside the Session context manager ensures that if an
    assertion fails, Session.__exit__ captures the error, screenshots, and writes
    the native failure callout to $GITHUB_STEP_SUMMARY.
    """
    if test_name is None:
        current = os.environ.get('PYTEST_CURRENT_TEST', '')
        test_name = current.split('::')[-1].split(' ')[0] or 'shortcut_guide_test'

    rec_dir = os.environ.get('WINTEGRATE_RECORD_DIR', 'recording-artifacts')
    artifact_dir = Path(rec_dir) / test_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with Session(config=SessionConfig(artifact_dir=artifact_dir, record_video=True, fps=15)) as session:
        exe_path = find_shortcut_guide_exe()
        exe_dir = os.path.dirname(os.path.abspath(exe_path))

        with session.step('prepare_environment'):
            sweep_processes_verified(('PowerToys.ShortcutGuide.exe',))

            h_trigger = kernel32.CreateEventW(None, False, False, TRIGGER_EVENT)
            h_winkey = kernel32.CreateEventW(None, False, False, WINKEY_EVENT)
            h_exit = kernel32.CreateEventW(None, False, False, EXIT_EVENT)

            assert h_trigger, f'Failed to create TRIGGER_EVENT: error={ctypes.get_last_error()}'
            assert h_winkey, f'Failed to create WINKEY_EVENT: error={ctypes.get_last_error()}'
            assert h_exit, f'Failed to create EXIT_EVENT: error={ctypes.get_last_error()}'

        with session.step('launch_process'):
            session.log_event('launch_shortcut_guide', f'Launching {exe_path} from cwd={exe_dir}')
            proc = subprocess.Popen(
                [exe_path],
                cwd=exe_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        with session.step('signal_activation'):
            session.log_event('signal_trigger_event', 'Signaling TRIGGER_EVENT')
            sig_ok = kernel32.SetEvent(h_trigger)
            assert sig_ok, f'SetEvent(h_trigger) returned False: error={ctypes.get_last_error()}'

            sg_win = Window.find(title_exact='Shortcut Guide', pid=proc.pid, timeout=15.0)
            session.log_event('window_activated', f'HWND={sg_win.hwnd:#x}, Title={sg_win.title!r}')

            sg_win.focus_content_island(timeout=5.0)

            search_box = sg_win.get_by_role('edit').first
            search_box.wait_for(state='visible', timeout=15.0)

        try:
            yield session, sg_win
        finally:
            with session.step('teardown'):
                kernel32.SetEvent(h_exit)
                kernel32.CloseHandle(h_trigger)
                kernel32.CloseHandle(h_winkey)
                kernel32.CloseHandle(h_exit)
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
                sweep_processes_verified(('PowerToys.ShortcutGuide.exe',))


# --- Control / Baseline Regression Tests (Must pass on both unpatched and patched builds) ---


def test_initial_focus_is_searchbox():
    """Control test: Shortcut Guide must place initial focus on the SearchBox."""
    with shortcut_guide_session('test_initial_focus_is_searchbox') as (session, sg_win):
        with session.step('verify_searchbox_focus'):
            focused = settled(
                UiaElement.get_focused,
                lambda el: el is not None and (el.automation_id == 'TextBox' or 'Search' in (el.name or '') or el.automation_id == 'ShortcutGuide_SearchBox'),
                timeout=5.0,
            )
            session.log_event('focus_check', f'Focused: {focused.name!r} (aid={focused.automation_id!r}, class={focused.class_name!r})')
            assert focused.automation_id == 'TextBox' or 'Search' in (focused.name or '') or focused.automation_id == 'ShortcutGuide_SearchBox', (
                f'Initial focus should be SearchBox, got: {focused.name!r} (aid={focused.automation_id!r})'
            )


def test_tab_to_close_button():
    """Control test: Tab 1 from SearchBox moves focus to CloseButton."""
    with shortcut_guide_session('test_tab_to_close_button') as (session, sg_win):
        with session.step('tab_to_close_button'):
            send_keys('{TAB}')
            focused = settled(
                UiaElement.get_focused,
                lambda el: el is not None and el.automation_id == 'CloseButton',
                timeout=3.0,
            )
            session.log_event('focus_check', f'Focused: {focused.name!r} (aid={focused.automation_id!r})')
            assert focused.automation_id == 'CloseButton', f'Tab 1 should be CloseButton, got: {focused.automation_id!r}'


def test_tab_to_navigation_rail_item():
    """Control test: Tab 2 moves focus to the first navigation rail item."""
    with shortcut_guide_session('test_tab_to_navigation_rail_item') as (session, sg_win):
        with session.step('tab_to_navigation_rail'):
            send_keys('{TAB}')
            time.sleep(0.1)
            send_keys('{TAB}')
            focused = settled(
                UiaElement.get_focused,
                lambda el: el is not None and el.control_type_id == 50007,
                timeout=3.0,
            )
            session.log_event('focus_check', f'Focused: {focused.name!r} (ctype={focused.control_type_id})')
            assert focused.control_type_id == 50007, f'Tab 2 should be NavigationViewItem, got: {focused.control_type_id}'


def test_tab_to_settings_button():
    """Control test: Tab 3 moves focus to the Settings button in the navigation rail."""
    with shortcut_guide_session('test_tab_to_settings_button') as (session, sg_win):
        with session.step('tab_to_settings'):
            for _ in range(3):
                send_keys('{TAB}')
                time.sleep(0.1)
            focused = settled(
                UiaElement.get_focused,
                lambda el: el is not None and el.name == 'Settings',
                timeout=3.0,
            )
            session.log_event('focus_check', f'Focused: {focused.name!r}')
            assert focused.name == 'Settings', f'Tab 3 should be Settings, got: {focused.name!r}'


def test_search_box_typing_filter():
    """Control test: Typing in SearchBox enters text query."""
    with shortcut_guide_session('test_search_box_typing_filter') as (session, sg_win):
        with session.step('type_in_searchbox'):
            search_box = sg_win.get_by_role('edit').first
            search_box.type_verified('Explorer', expected_line_count_delta=0, verify_contains='Explorer')


def test_escape_key_dismissal():
    """Control test: Pressing Escape dismisses the Shortcut Guide overlay."""
    with shortcut_guide_session('test_escape_key_dismissal') as (session, sg_win):
        with session.step('dismiss_via_escape'):
            send_keys('{ESC}')
            focused = settled(
                UiaElement.get_focused,
                lambda el: el is None or el.automation_id != 'TextBox',
                timeout=3.0,
            )
            session.log_event('focus_after_esc', f'Focused after ESC: aid={focused.automation_id if focused else None!r}')
            assert focused is None or focused.automation_id != 'TextBox', 'Focus should leave the Shortcut Guide search box after Esc'


# --- Target Behavior Test ---


def test_shortcut_guide_tab_order_reaches_shortcuts():
    """Target test: Tab 4 must reach the shortcut cards list, not skip to an unnamed container."""
    with shortcut_guide_session('test_shortcut_guide_tab_order_reaches_shortcuts') as (session, sg_win):
        with session.step('tab_to_shortcuts'):
            for _ in range(4):
                send_keys('{TAB}')
                time.sleep(0.1)
            t4 = settled(
                UiaElement.get_focused,
                lambda el: el is not None and (el.name or '') != '',
                timeout=2.0,
            )
            session.log_event('focus_tab_4', f'Focused after 4 TABs: name={t4.name!r} class={t4.class_name!r} ctype={t4.control_type_id}')
            assert t4.control_type_id != 50033 and (t4.name or '') != '' and t4.name != 'Start', (
                f'Tab 4 skipped shortcut list and landed on unnamed Pane stop: '
                f'name={t4.name!r} class={t4.class_name!r} ctype={t4.control_type_id}'
            )


def test_tab_from_settings_reaches_shortcuts():
    """Target test: Tab from Settings button must reach the shortcut cards list."""
    with shortcut_guide_session('test_tab_from_settings_reaches_shortcuts') as (session, sg_win):
        with session.step('navigate_to_settings'):
            for _ in range(3):
                send_keys('{TAB}')
                time.sleep(0.1)
            settings = settled(
                UiaElement.get_focused,
                lambda el: el is not None and el.name == 'Settings',
                timeout=3.0,
            )
            assert settings.name == 'Settings', f'Failed to reach Settings: {settings.name!r}'

        with session.step('tab_to_first_shortcut'):
            send_keys('{TAB}')
            card = settled(
                UiaElement.get_focused,
                lambda el: el is not None and (el.name or '') != '',
                timeout=2.0,
            )
            session.log_event('focus_after_tab', f'Focused: name={card.name!r} class={card.class_name!r} ctype={card.control_type_id}')
            assert card.control_type_id != 50033 and (card.name or '') != '' and card.name != 'Start', (
                f'Tab from Settings skipped shortcut list and landed on unnamed Pane stop: '
                f'name={card.name!r} class={card.class_name!r} ctype={card.control_type_id}'
            )


def test_shortcut_card_arrow_navigation():
    """Target test: Down Arrow navigates sequentially between shortcut cards."""
    with shortcut_guide_session('test_shortcut_card_arrow_navigation') as (session, sg_win):
        with session.step('tab_to_shortcuts'):
            for _ in range(4):
                send_keys('{TAB}')
                time.sleep(0.1)
            first_card = settled(
                UiaElement.get_focused,
                lambda el: el is not None and (el.name or '') != '',
                timeout=2.0,
            )
            assert first_card.control_type_id != 50033 and (first_card.name or '') != '', (
                f'Focus failed to reach first shortcut card: {first_card.name!r}'
            )

        with session.step('arrow_down_to_next'):
            send_keys('{DOWN}')
            second_card = settled(
                UiaElement.get_focused,
                lambda el: el is not None and el.name != first_card.name and (el.name or '') != '',
                timeout=2.0,
            )
            session.log_event('focus_after_down', f'Focused: name={second_card.name!r}')
            assert second_card.name != first_card.name and (second_card.name or '') != '', (
                f'Arrow Down failed to move to next shortcut card, remained on {second_card.name!r}'
            )
