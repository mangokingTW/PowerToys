# Automated reproduction of Command Palette issue #50175

[#50175](https://github.com/microsoft/PowerToys/issues/50175) — a bookmark whose
URL contains a `{placeholder}` shows the field already filled in with the last
value entered, every subsequent time it is opened.

This directory holds one reproduction of that, driven through UI Automation. It
is not a proposal to adopt a second test framework — see *Relationship to
`Microsoft.CmdPal.UITests`* below.

## Running it

```
pip install "wintegrate[video]" pytest
pytest src/modules/cmdpal/Tests/ui-repro -v -rxX -s
```

Command Palette must have been started at least once on the machine: the
`Microsoft.CommandPalette` MSIX is registered on PowerToys' **first run**, not by
the installer. The test says so explicitly rather than failing obscurely if it
has not.

The test seeds its own bookmark by writing
`%LOCALAPPDATA%\Packages\Microsoft.CommandPalette_8wekyb3d8bbwe\LocalState\bookmarks.json`,
so it does not depend on driving the add-bookmark UI. **Any bookmarks already on
the machine are overwritten**, which is worth knowing before running it on a
desktop rather than a runner.

## What it measures

| step | placeholder field |
| --- | --- |
| open the bookmark for the first time | `''` |
| type `1` | `'1'` |
| launch, then open the same bookmark again | `'1'` — expected `''` |
| restart Command Palette, open it again | `''` |

Measured on Command Palette 0.12.12365.0 (PowerToys 0.101.2362.0), Windows 11
26100, on both x64 and ARM64.

Five assertions, four of which are controls:

- the field starts empty on the first open;
- typing into it is visible to the test — without this, a reading that always
  came back empty would make the assertion above pass while measuring nothing;
- the palette is back at its list between the two opens, so the second open
  really is a second open and not the first form still on screen;
- the value is not in `bookmarks.json` afterwards, and a restart clears it;
- **the reproduction**: the field is empty when the bookmark is opened again.

That last one is `xfail(strict=True)`. It asserts the *wanted* behaviour, so:

- **the run is green while the issue reproduces**;
- if the behaviour changes, the test XPASSes and `strict=True` turns the run
  **red** — the signal that this file has done its job and can be deleted;
- a failing control also turns the run red, which says the measurement is not
  trustworthy rather than that the issue is fixed.

The fourth control is the one that corroborates the root cause already traced on
the issue. The value is never written to `bookmarks.json` and does not survive a
restart, so it is state cached in the session — consistent with
`BookmarkPlaceholderPage` and its `StringParameterRun` being built once in
`BookmarkListItem`'s constructor and never reset after a launch, and *not*
consistent with the value having been saved as the bookmark's default.

## Relationship to `Microsoft.CmdPal.UITests`

Command Palette already has a UI test project, and this reproduction uses the
same locator it does: `CommandPaletteTestBase.SetSearchBoxText` finds
`By.AccessibilityId("MainSearchBox")`, which is exactly how the search box is
reached here. Porting this to `Microsoft.CmdPal.UITests` is mostly mechanical.

One thing does not port cleanly, and it is worth fixing regardless of this
issue. The placeholder `TextBox` comes from `StringParamTemplate` in
`SearchBar.xaml`:

```xml
<DataTemplate x:Key="StringParamTemplate" x:DataType="coreVm:StringParameterRunViewModel">
    <TextBox
        VerticalAlignment="Center"
        KeyDown="StringParameter_KeyDown"
        PlaceholderText="{x:Bind PlaceholderText, Mode=OneWay}"
        Style="{StaticResource SearchParameterTextBoxStyle}"
        Text="{x:Bind TextForUI, Mode=OneWay}"
        TextChanged="StringParameter_TextChanged" />
</DataTemplate>
```

It has no `x:Name` and no `AutomationProperties.AutomationId`, so it cannot be
found by id by any UI Automation client — including `By.AccessibilityId`. This
test identifies it as *the only Edit in the window that is not `MainSearchBox`*,
which works only because the `SwitchPresenter` swaps `MainSearchBox` out in the
`Parameters` case. Giving that `TextBox` an automation id would make it directly
addressable from the existing harness.

## Notes for anyone re-running this

Three details that took experiments rather than reading to settle, recorded so
nobody has to repeat them:

- **The bookmark file is in `LocalState`, not `LocalCache\Local`.**
  `Utilities.BaseSettingsPath` calls `SHGetKnownFolderPath` with
  `KF_FLAG_FORCE_APP_DATA_REDIRECTION`, and for a packaged process that resolves
  to the package's `LocalState`. Seeding both candidates with different bookmark
  names and seeing which one appeared in the palette is what settled it.
- **The `"Microsoft.CmdPal"` folder name is not part of the path** when packaged
  — `BaseSettingsPath` appends it only `if (!IsPackaged())` — so the file sits
  directly in `LocalState`, not in a subdirectory.
- **The bookmark points at a local `.txt`, not an `https` URL.** The issue's own
  repro uses a URL, and the retention is identical either way — it does not
  depend on what gets launched. But an `http` bookmark starts the default
  browser, and on a machine with no default browser Windows puts up a *"How do
  you want to open this?"* modal instead;
  `CommandPaletteTestBase.FindDefaultAppDialogAndClickButton` exists because
  that really happens. A `.txt` has a handler everywhere, so the launch is
  predictable and nothing here touches the network.

## Why `wintegrate`

[`wintegrate`](https://github.com/mangokingTW/wintegrate) is a library written to
make Windows GUI behaviour measurable from CI, where there is no one to watch the
screen. Its bias is that every step either verifies itself or reports that it
could not: a click with no rectangle to aim at raises instead of silently doing
nothing, and a window operation is confirmed against Win32 rather than assumed.

Two gaps in it turned up while writing this reproduction, both now known:
`send_keys()` has no Win-key modifier in its grammar (so Win+Alt+Space goes
through `send_vk_input()` by virtual key), and `get_value()` falls back to an
element's Name when no text pattern answers — which here would have reported an
empty field as `'n'`, the placeholder key. `_read_field` refuses that fallback
explicitly rather than trusting it.

## The recording

`WINTEGRATE_RECORD=1` records the screen for the duration of the run and writes
`recording-artifacts/reproduction-50175-<arch>.mp4`. The workflow in
`.github/workflows/wintegrate-repro-50175.yml` sets it and keeps the artifact
whatever the outcome. The video is not the evidence — the assertions are — but a
maintainer reading a table of strings still has to take it on trust, and the
recording is what answers *does this look like the bug I reported?*
