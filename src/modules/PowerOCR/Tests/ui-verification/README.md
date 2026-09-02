# PowerToys Text Extractor (PowerOCR WinUI 3) UI Automation Verification

This test suite verifies the WinUI 3 migration of Text Extractor (PR [#49431](https://github.com/microsoft/PowerToys/pull/49431) / Issue [#49656](https://github.com/microsoft/PowerToys/issues/49656)).

## Background & Blocker

The WinUI 3 migration of Text Extractor replaces WPF with `WinUIEx.WindowEx`, `Canvas`, and native WinUI 3 controls. However, existing UI automated tests (`PowerOCR-UITests`) rely on WinAppDriver, which fails on headless CI / detached desktop environments because WinAppDriver cannot inject inputs into unattached sessions.

## How `wintegrate` Resolves the Blocker

Using [`wintegrate`](https://github.com/mangokingTW/wintegrate):
1. **Zero WinAppDriver Dependency**: Direct Win32 / UIA / SendInput bindings allow deterministic execution on headless GitHub-Hosted runners (`windows-latest` & `windows-11-arm`).
2. **Smooth Pointer Drag Interpolation**: Simulates discrete `PointerMoved` events to update the 4 mask rectangles and region selection border on `RegionClickCanvas`.
3. **Full Visual Black-Boxing**: Automatically records MP4 video with mouse position and keyboard input HUD overlays for CI diagnostic artifacts.

## Running Locally

```bash
pip install "wintegrate[video]>=0.5.5" pytest
pytest src/modules/PowerOCR/Tests/ui-verification -v -s
```
