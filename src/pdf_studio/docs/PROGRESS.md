# PDF Studio — ticket progress

The app was built in 42 tickets across five milestones; this file records
what each one delivered and where it lives in `src/pdf_studio`.

Run the tests: `python -m pytest -q` from the repo root (see the README for
the development setup).

## Milestone 1 — Reader

| # | Ticket | Status | Notes |
|---|---|---|---|
| 01 | Architecture doc + scaffold + engine facade | done | `docs/ARCHITECTURE.md`, `pdf/engine.py` |
| 02 | App shell: open a PDF | done | `ui/main_window.py`, `ui/empty_state.py` |
| 03 | Virtualized viewer + coords + render cache | done | tiled rendering, `core/geometry.py`, `core/render_cache.py`, `core/render_queue.py` |
| 04 | Zoom, pan, nav, status bar, shortcuts | done | floating page/zoom pill, automatic zoom |
| 05 | Document tabs | done | per-tab `DocumentView` state |
| 06 | Lazy thumbnails | done | rendered on paint, keyed by stable page id |
| 07 | Async search | done | streaming results, match case / whole words |
| 08 | Bookmarks / outline | done | follows current page |
| 09 | Preferences + Open Recent | done | `ui/settings.py` |
| 10 | Milestone 1 tests + samples | done | `tests/fixtures/make_fixtures.py` |

## Milestone 2 — Annotate & save

| # | Ticket | Status | Notes |
|---|---|---|---|
| 11 | Command stack + dirty flag | done | `core/commands.py`; Undo/Redo with labels; `name.pdf *` |
| 12 | Save / Save As + close prompts | done | incremental when safe, clean rewrite otherwise; undo survives save |
| 13 | Text selection | done | drag / double-click word / triple-click line, Ctrl+C, context menu |
| 14 | Annotation engine API | done | `pdf/annotations.py`, displayed-space models, /NM ids stable |
| 15 | Tool controller + markup tools | done | highlight / underline / strikethrough; one-shot on existing selection |
| 16 | Notes, free text, ink | done | + rectangle / ellipse / line / arrow tools |
| 17 | Select / move / delete + properties | done | handles, nudge, floating inspector (also edits tool defaults) |
| 18 | Annotation overlay rendering | done | page re-render keyed by revision, stale-while-revalidate |
| 19 | Milestone 2 tests | done | `tests/test_milestone2.py` |

## Milestone 3 — Organize pages

| # | Ticket | Status | Notes |
|---|---|---|---|
| 20 | Page operations engine API | done | `pdf/page_operations.py`; session keeps stable page ids |
| 21 | Undoable page-ops commands | done | `core/page_commands.py`; delete restores content + outline |
| 22 | Pages menu + viewer sync | done | acts on thumbnail multi-selection or current page |
| 23 | Thumbnail reorder + context menu | done | drag to reorder (no re-raster), Delete key, right-click menu |
| 24 | Insert blank + insert from PDF | done | page-picker dialog, password prompt, drop PDFs on thumbnails |
| 25 | Extract, split, combine | done | `pdf/documents.py`; combine into new tab or append (one undo step) |
| 26 | Milestone 3 tests | done | `tests/test_milestone3.py` |

## Milestone 4 — Edit content & sign

| # | Ticket | Status | Notes |
|---|---|---|---|
| 27 | Redact-and-replace engine API | done | `pdf/editing.py`: real text removal, style + background probing, shrink-to-fit |
| 28 | Undoable redact / replace commands | done | `core/edit_commands.py`; page-content snapshots |
| 29 | Redact / Replace tool UI | done | Edit Text (dialog with live preview) and Redact tools |
| 30 | Image cover / replace engine API | done | bytes or path; non-PNG/JPEG converted |
| 31 | Image replace tool UI | done | click an image to replace it, or drag a box |
| 32 | Stamp / signature engine | done | `pdf/stamps.py`; image-stamp annotations, trim + white-removal |
| 33 | Signature / stamp tool UI | done | Sign tool with ghost preview; saved / draw / import / stamps |
| 34 | Milestone 4 tests | done | `tests/test_milestone4.py` |

## Milestone 5 — Polish, performance, packaging

| # | Ticket | Status | Notes |
|---|---|---|---|
| 35 | Render cache eviction + performance | done | byte/entry-bounded LRU, stale zoom levels shed, capped side caches |
| 36 | Single-page and two-page view | done | Ctrl+4/5/6, floating-bar picker, wheel page-flip, persisted |
| 37 | Light / dark theme | done | System / Light / Dark, Ctrl+Shift+L, native title bar, persisted |
| 38 | Encrypted PDF password UX | done | prompt + retry, cancel leaves no tab, encryption kept on save |
| 39 | Packaging + launcher | done | `pdf-studio` / `pdf-studiow`, `scripts/build_pdf_studio.ps1 -Smoke` |
| 40 | Shell UX polish | done | Help menu, About, shortcut cheat sheet (F1), richer empty state |
| 41 | Signature ink-capture dialog | done | draw pad: smoothing, ink colors/widths, undo, save for reuse |
| 42 | Milestone 5 tests | done | `tests/test_milestone5.py` |

**Totals:** 42/42 tickets · 368 automated tests (`python -m pytest -q`, ~50 s).

## Beyond the tickets — Windows desktop install

- `PDFStudio.exe --install` / `--uninstall` (or **Help → PDF Studio on This Computer**):
  per-user install to `%LOCALAPPDATA%\Programs\PDF Studio`, Desktop + Start-menu shortcuts,
  `.pdf` registration (Open with, Default apps, Settings → Apps entry). No admin rights.
- Single instance: files opened from Explorer go to the running window as tabs.
- **Help → Check for Updates** (`core/updates.py`, `ui/updates.py`, `ui/dialogs/updates.py`):
  asks GitHub for the latest release, shows its notes, downloads the file for this computer
  and checks its SHA-256. The app's only network access, and only on request.
- In-place updates on Windows (`integration/updater.py`): the new version, started with
  `--apply-update`, waits for the old one to exit, swaps the app folder with renames (a
  failure keeps the old version) and reopens the documents.
- Release builds use a clean uv venv (`build\venv`, pinned in
  `packaging/requirements-build.txt`); bundle trimmed to ~105 MB (46 MB zipped).
