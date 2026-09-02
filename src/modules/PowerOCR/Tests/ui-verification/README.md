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

## What the test actually asserts

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

## Two preconditions the test controls rather than assumes

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
