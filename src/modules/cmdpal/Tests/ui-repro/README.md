# Automated reproduction of Command Palette issue #45816

[#45816](https://github.com/microsoft/PowerToys/issues/45816) — keyboard
shortcuts stop working after a click inside the palette, and only closing and
reopening it recovers.

Labelled `Status-Reproducible`. This directory holds one reproduction of it,
driven through UI Automation. It is not a proposal to adopt a second test
framework — see *Relationship to `Microsoft.CmdPal.UITests`* below.

## Running it

```
pip install "wintegrate[video]" pytest
pytest src/modules/cmdpal/Tests/ui-repro -v -rxX -s
```

Command Palette must have been started at least once on the machine: the
`Microsoft.CommandPalette` MSIX is registered on PowerToys' **first run**, not by
the installer. The test says so explicitly rather than failing obscurely.

The test seeds its own bookmark by writing
`%LOCALAPPDATA%\Packages\Microsoft.CommandPalette_8wekyb3d8bbwe\LocalState\bookmarks.json`,
so **any bookmarks already on the machine are overwritten**. Worth knowing before
running it on a desktop rather than a runner.

## No extension is needed

The report reproduces on an extension page, because those have no search input
to focus:

> Keyboard shortcuts in Command Palette only seem to work if the search input box
> is focused. However, some pages in Command Palette do not have a search input to
> focus.

Enumerating the built-in top-level commands and checking each resulting page for
`MainSearchBox` turns up several that do not have one — the bookmark
`{placeholder}` form among them. That page is enough: focus starts on the form's
own text box, and a single click moves it off. So nothing is installed and nothing
is downloaded, and the reproduction stays first-party.

## What it measures

Command Palette 0.12.12365.0 (PowerToys 0.101.2362.0), Windows 11 26100:

| step | on list page | cloaked | focused |
| --- | --- | --- | --- |
| palette open | `True` | `False` | `MainSearchBox` |
| form page opened | `False` | `False` | the form `TextBox` |
| **Esc, no click yet** | **`True`** | `False` | `MainSearchBox` |
| form page again | `False` | `False` | the form `TextBox` |
| clicked the footer label | `False` | `False` | `InputSiteWindowClass` |
| **Esc after the click** | **`False`** | `False` | `InputSiteWindowClass` |
| **Enter after the click** | **`False`** | `False` | `InputSiteWindowClass` |
| Esc three more times | `False` | `False` | `InputSiteWindowClass` |
| palette closed and reopened | `True` | `False` | `MainSearchBox` |

Row three is the control that makes row six mean anything: the same key, on the
same page, differing only in whether a click happened first.

`InputSiteWindowClass` is the WinUI content island's own container. Focus lands
there rather than on any control, which matches the report's claim that the
shortcuts only work while something that handles them has focus.

Five assertions pass and two are the reproduction:

- the palette opens on its list page with the search box focused;
- the form page has no `MainSearchBox`, i.e. the report's precondition holds;
- **Esc returns to the list page when no click happened** — the control;
- the click strands focus on `InputSiteWindowClass`, so the click did what the
  report describes rather than missing;
- reopening restores the shortcuts, which is both the report's recovery claim and
  the proof the process had not simply died.

The two reproductions are `xfail(strict=True)`. They assert the *wanted*
behaviour, so:

- **the run is green while the issue reproduces**;
- if the behaviour changes, they XPASS and `strict=True` turns the run **red** —
  the signal this directory has done its job and can be deleted;
- a failing control also turns the run red, which says the measurement is not
  trustworthy rather than that the issue is fixed.

## Two things worth knowing if you re-run this

- **`IsWindowVisible` cannot tell whether the palette is on screen.** It answers
  `True` whether the palette is showing or hidden, because Command Palette hides
  by DWM *cloaking*. `DWMWA_CLOAKED` is the read that answers, and the test
  carries it directly since `wintegrate` has no cloaking API yet. An earlier
  version of this used `IsWindowVisible` and its *control* failed — Esc had
  demonstrably worked and the measurement said otherwise.
- **The click target is found by geometry, not by name.** The footer reads "Open"
  in English and something else in every other language. It also has to be an
  inert `Text` element: an earlier version clicked a label belonging to a command
  button, which navigated somewhere instead of stranding focus and so measured
  nothing.

## Relationship to `Microsoft.CmdPal.UITests`

Command Palette already has a UI test project, and this reproduction uses the
same locator it does: `CommandPaletteTestBase.SetSearchBoxText` finds
`By.AccessibilityId("MainSearchBox")`, which is exactly how the search box is
reached here. Porting this is mostly mechanical.

The parts that would need something new are the two Win32 reads above —
`DWMWA_CLOAKED` for "is the palette on screen", and the identity of the focused
element — neither of which is expressible as an element locator.

## Why `wintegrate`

[`wintegrate`](https://github.com/mangokingTW/wintegrate) is a library written to
make Windows GUI behaviour measurable from CI, where there is no one to watch the
screen. Its bias is that every step either verifies itself or reports that it
could not: a click with no rectangle to aim at raises instead of silently doing
nothing, and a window operation is confirmed against Win32 rather than assumed.

## The recording

`WINTEGRATE_RECORD=1` records the screen for the run and writes
`recording-artifacts/reproduction-45816-<arch>.mp4`. The workflow in
`.github/workflows/wintegrate-repro-45816.yml` sets it and keeps the artifact
whatever the outcome. The video is not the evidence — the assertions are — but a
maintainer reading a table of booleans still has to take it on trust, and the
recording is what answers *does this look like the bug I confirmed?*
