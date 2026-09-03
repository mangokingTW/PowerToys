# Text Extractor (PowerOCR WinUI 3) — unattended UI verification

Runs one interactive check from PR [#49431](https://github.com/microsoft/PowerToys/pull/49431)
on a stock GitHub-hosted runner: summon the Text Extractor overlay, drag a selection
rectangle across it, and verify the text that comes out.

## The blocker this works around

That PR reports **15 PASS, 0 FAIL, 16 BLOCKED**, and the blocked items need an attached
interactive desktop, WinAppDriver v1.2.1, and a real pointer drag. `PowerOCR-UITests`
compiles but cannot run: WinAppDriver dispatches input through focus, and a detached
input desktop has none to dispatch to.

[`wintegrate`](https://github.com/mangokingTW/wintegrate) drives UIA and `SendInput`
in-process, with no driver and no daemon, so the same interaction runs on
`windows-latest` and `windows-11-arm` with nothing attached.

## The flagship: what the region test asserts

The point is the **bounds** of the selection, not that OCR returned something.

Two Notepad windows are placed at known rectangles with an 80px gap between them:

```
+--------------------------------------------------+
| Selected region inside the band                  |  <- band covers the top
+--------------------------------------------------+     half of this editor
                   (80px gap)
+--------------------------------------------------+
| Excluded sentence further down                   |  <- must not appear
+--------------------------------------------------+
```

The drag covers the top half of the first window's editor. The extracted text is then
read back **through UI Automation from a destination Notepad** — not from the clipboard,
which is what PowerOCR writes and would only prove it talked to itself — and asserted
to equal the first line exactly, after collapsing whitespace and case.

That one equality fails in both directions:

- a selection that is too small, or offset, loses part of the line;
- a selection that is too large picks up the second window's line.

Two earlier versions of this test passed while visibly wrong, which is why the
assertion is an equality rather than a "not empty" check:

| what it read back | what was wrong |
| --- | --- |
| `erministic UI Automation by wintegrate` | the band started 3 characters into the line |
| `... 100% WINDOWS (CRLF) UTF-8` | the band ran past the editor into Notepad's status bar |

## The other checklist items covered here

`test_powerocr_toolbar_and_keyboard.py` covers five more, all through UI Automation
with no driver and nothing attached. The toolbar's AutomationIds are present and
carry the patterns needed, on the shipped build as well as on main:

| checklist item | how |
| --- | --- |
| *four toolbar buttons each have a non-empty accessible name* | read `Name`; non-empty is the whole assertion, because the names are localized |
| *toggle Single-line mode; the button reports Selected* | `TogglePattern`, state read back rather than assumed |
| *toggle Table mode; the button reports Selected* | same |
| *tab through the toolbar; each control reachable without a mouse* | send Tab, read the focused element each time. Observed order: language combo, Single-line, Table, Settings, Cancel, then wraps |
| *Escape after toggling modes dismisses the overlay* | both modes on, then Escape, then the overlay must be gone |
| *open the language list from the keyboard; items accessible* | **Alt+Down**, not the Shift+F10 the checklist names -- see below |

### Single-line mode, and one line the checklist describes differently

`test_powerocr_capture_modes.py` covers the single-line capture mode as a
*differential*: the same two-line selection, dragged twice, with only the toggle
changing.

```
single-line off: 'Harbour lights at dusk\r\nFerries cross the water'
single-line on:  'Harbour lights at dusk Ferries cross the water'
```

The checklist line reads *"activate Single-line, click a single line of text;
verify one line is on the clipboard"*, but the button's own accessible name is
**"Format result as a single line"** -- the mode is about how a multi-line result
is joined, not about which line gets picked. The differential is what the button
says it does, and it cannot pass by accident: one run cannot distinguish "the mode
works" from "the recogniser returned one line anyway".

### Clicking a word without dragging: measured, not covered

The checklist's *"click a single word or character without dragging"* is not
covered, because no gesture tried produced a result. Five deliveries, each against
a freshly raised overlay with the cursor inside the same word:

| delivery | result |
| --- | --- |
| `Mouse.click()` | nothing |
| move, down, hold 0.35s, up | nothing |
| tiny drag (6px) | nothing |
| **drag across the whole word** | **`Kestrel`** |
| click 8px lower | nothing |

So a drag over that word works and a click on it does not, within a 12s wait. That
line is unchecked in the checklist too, so this is a question for whoever knows the
intended gesture rather than a claim about the product.

### One place the checklist's wording is out of date

The language item says to open the flyout with Shift+F10 or the right-click key.
Measured on the shipped build, each gesture against a freshly raised overlay with
the combo focused but *not* clicked:

```
Alt+Down    collapsed -> expanded    (items 0 -> 2)
F4          collapsed -> expanded    (items 0 -> 2)
Space       collapsed -> collapsed
Shift+F10   collapsed -> collapsed
Apps key    collapsed -> collapsed
```

The chooser is a toolbar ComboBox, and Alt+Down / F4 are its keyboard affordances;
Shift+F10 belongs to the right-click context menu that line was written for. The
item's intent holds -- the list is reachable without a mouse -- and its letter does
not.

Two measurement traps on the way there, both of which made an earlier version of
that test assert nothing:

- `set_focus()` clicks by default. That opens the combo with the mouse, so the list
  was already expanded before any key was sent. Focus is now taken with
  `click=False` and the collapsed state asserted first.
- a collapsed ComboBox reports 0 items, but one opened by a click reports 2. So the
  item count measures how it was opened, not whether it is open; the assertion is on
  `ExpandCollapseState`.

All three tests that depend on a keystroke landing were checked by removing the
keystroke and nothing else. Each one fails when it is gone.

## Two preconditions the tests control rather than assume

- **OCR language.** `PreferredLanguage` in `%LOCALAPPDATA%\Microsoft\PowerToys\TextExtractor\settings.json`
  is pinned to `English (United States)` before launch. PowerOCR otherwise picks its
  recogniser from the host's language preference: the local zh-TW ARM64 VM returned
  Chinese glyphs for this line, with both recognisers installed. Without pinning, an
  exact assertion passes on an en-US runner by luck.
- **Source window content.** Notepad restores the previous session's tabs, so a freshly
  launched window is only empty on a machine that has never used it. The editor is
  cleared through UIA `SetValue` — `Ctrl+A` followed by `Delete` does *not* clear it
  (measured: 261 characters down to 234) — and the test then asserts the source holds
  the expected line before it drags, so a polluted host fails loudly instead of
  asserting against unknown text.

## Running locally

```bash
pip install "wintegrate[video]>=0.5.6" pytest
pytest src/modules/PowerOCR/Tests/ui-verification -v -s
```

`>=0.5.6` is not arbitrary: the `Mouse` controller this test uses landed in 0.5.6, and
0.5.5 installs cleanly and then fails on import.

Set `WINTEGRATE_RECORD=1` to get an MP4 of the run, with the pointer and its click
markers drawn into each frame. CI always sets it and uploads the result.
