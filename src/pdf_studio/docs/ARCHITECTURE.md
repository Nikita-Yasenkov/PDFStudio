# PDF Studio — Architecture

PDF Studio is a desktop PDF viewer/editor built on **PyQt6** (UI) and
**PyMuPDF** (rendering, text, annotations, page and content edits).
Dependencies are deliberately minimal: PyQt6 and PyMuPDF at runtime;
Pillow is *not* required (PyMuPDF handles PNG/JPEG).

## 1. Layers

```
pdf_studio/
  pdf/    Qt-free PyMuPDF facade      (the only code that imports pymupdf)
  core/   Qt-free application logic   (sessions, commands, layout, caches)
  ui/     PyQt6 widgets               (never imports pymupdf)
```

Imports only flow downwards (`ui → core → pdf`). The `pdf` layer returns
plain frozen dataclasses (`PageInfo`, `Word`, `SearchHit`,
`AnnotationModel`, `RenderedImage`, …); PyMuPDF `Document`/`Page`/`Annot`
handles never leave it. Tests enforce that `ui/` does not import `fitz` or
`pymupdf`.

| Package | Key modules |
|---|---|
| `pdf` | `engine.PdfDocument` (open, info, render, text, outline, search, save), `annotations`, `page_operations`, `editing` (redact/replace, image cover, content snapshots), `documents` (extract/split/merge files) |
| `core` | `session.DocumentSession`, `commands.CommandStack`, `*_commands`, `geometry` (coordinate transforms), `layout` (view modes), `zoom`, `render_cache`, `render_queue`, `selection`, `page_ranges` |
| `ui` | `main_window`, `document_view` (one per tab), `canvas` (virtualized page view), `render_service` (worker thread), `tools` (ToolController + tools), sidebar panels, `properties_panel`, `theme`, `icons`, `settings`, dialogs |

## 2. Coordinates

There is exactly one page coordinate system visible above the `pdf`
layer: **displayed page space** — PDF points, origin top-left of the page
*as displayed* (after `/Rotate`), y down. The engine converts to/from
PyMuPDF's unrotated space with `page.rotation_matrix` /
`derotation_matrix` at its boundary (text, search, annotations, edits).

Screen mapping is centralised in `core/geometry.py`:

```
pdf point ──× scale──▶ page-local px ──+ slot origin──▶ canvas px ──− scroll──▶ widget px
scale = zoom × 96/72   (100 % = physical size on a 96-dpi screen)
device px = logical px × devicePixelRatio
```

`widget_to_pdf` / `pdf_to_widget` (and the rect variants) are the only
functions tools and overlays use; nobody re-derives the math.

## 3. Rendering and virtualization

* `core/layout.py` computes page *slots* (canvas rects) for the current view
  mode (continuous, single page, two-page spreads), zoom and viewport.
  Pages are positioned on whole logical pixels.
* The canvas (`ui/canvas.py`, a `QAbstractScrollArea`) paints only slots
  that intersect the viewport. Each page is split into **512-device-pixel
  tiles**; only visible tiles (plus a one-viewport buffer) are requested. At
  any zoom, memory is bounded by viewport size, not page or document size.
* Tiles are rendered from a cached MuPDF **display list** per page (parsed
  once, rasterised per tile) at `scale × DPR`.
* `core/render_queue.RenderQueue` (Qt-free, thread-safe) holds pending
  requests per *owner* (a canvas or a thumbnail panel). Each repaint
  **replaces** the owner's pending set, so tiles for an old zoom/scroll are
  dropped before they render (obsolete-job cancellation). Requests carry the
  page id + revision; the worker re-resolves the page under the engine lock
  and skips the job if the page changed or vanished.
* `ui/render_service.RenderService` runs one worker `QThread`; results come
  back to the UI thread as `QImage` via a queued signal.
* `core/render_cache.RenderCache` is an LRU bounded by entry count and an
  approximate byte budget, keyed by `(doc, page_id, revision, scale, tile)`.
  While a tile is missing, the canvas draws the best cached raster of the
  same page at another scale (or the thumbnail), so zooming and edits never
  flash white.

### Threading rules

MuPDF is not thread-safe and PyMuPDF releases the GIL while rasterising,
so **all** PyMuPDF access is serialised by the global re-entrant
`pdf.engine.ENGINE_LOCK`. Background work (tile renders, thumbnails,
search) takes it per tile/page, so UI-thread calls wait at most one short
job. Commands hold the lock for their whole execute/undo, making page-id
resolution and mutation atomic with respect to rendering.

## 4. Documents, sessions and tabs

* `DocumentSession` (Qt-free) owns one `PdfDocument`, its `CommandStack`,
  path, dirty state, **stable page ids** (survive reorder/insert/delete so
  caches and thumbnails don't re-raster after a drag), per-page
  **revisions** (bumped on any content/annotation change), and cached
  words/annotations/outline. It is the single entry point for mutations and
  publishes `SessionEvent`s (structure, pages, annotations, history, dirty,
  path) to subscribers.
* Each tab is a `DocumentView` holding a session plus its own view state
  (zoom mode, zoom, scroll, current page, view mode, selection). There is no
  global "current document" — the main window asks the active view.

## 5. Text selection

Selection is word based, using `page.get_text("words")` (in displayed
space). A drag selects all words in reading order (block, line, word) between
the word nearest the press point and the word nearest the current point;
line boxes are merged per line for display and for markup quads. Copy joins
words with spaces and lines with newlines. No OCR: scanned pages without a
text layer simply have nothing to select.

## 6. Annotations vs content editing

| Feature | Mechanism | Undo strategy |
|---|---|---|
| Highlight / underline / strikeout / squiggly, notes, free text, ink, shapes, line/arrow, stamps & signatures | Native PDF annotations via PyMuPDF (`add_*_annot`) | Model snapshots. Annotations are identified by their `/NM` id, which is re-applied when an annotation is recreated, so later commands keep working. Moving ink/line recreates with translated vertices (MuPDF forbids `set_rect` on them). |
| Redact & replace text, redact (black box), image cover/replace | Real content edits: `apply_redactions` (text removed, images and vector art untouched) + `insert_textbox` / `insert_image` | **Content snapshot**: before the edit, the page's `/Contents` and `/Resources` entries plus every content stream and form XObject reachable from them are captured (source + decompressed bytes). Undo writes them back. |
| Page rotate / delete / duplicate / move / insert / insert-from-PDF | `set_rotation`, `delete_page`, `fullcopy_page`, `select` (permutation), `new_page`, `insert_pdf` | Inverse operation; deleted pages are kept as a one-page PDF in memory and re-inserted, and the outline (TOC) is restored. |

Redaction is real: the replaced text is removed from the content stream,
so it no longer appears in search or extraction. Replacement is **box
bound**, with no reflow: the new text is set in a base-14 font with the
probed size/colour inside the original box.

Visual signatures are **image stamp annotations**, not cryptographic
signatures. PKI signing is out of scope.

## 7. Undo / redo

`core/commands.py` provides `Command` (`execute`, `undo`, `redo`, optional
`merge_with` for slider-style edits) and a `CommandStack` with a clean
index. A command that raises during `execute` is not pushed, and the stack
and dirty flag stay unchanged. Every command is Qt-free and only talks to the
session/engine API. Each tab has its own stack.

## 8. Saving

* **Save** to the file the document was opened from uses an **incremental
  save** when PyMuPDF allows it and no destructive content edit has happened
  since the last save. Incremental saves keep existing digital signatures
  valid and are fast.
* Otherwise, and for **Save As**, the document is serialised without garbage
  collection (object numbers preserved), re-cleaned into a temporary file
  with `garbage=3, deflate=True`, then atomically moved over the target.
  If the target is the file backing the open document (Windows keeps it
  locked), the session first swaps to an in-memory copy with identical
  object numbers, so undo history stays valid after saving.
* Encrypted documents keep their encryption (`PDF_ENCRYPT_KEEP`).
* The original file is never touched until the user explicitly saves.

## 9. Known limitations

* No text reflow or true in-place font editing. Replacement text uses a
  base-14 font sized to the original box.
* Word-granular selection, with no OCR for scanned pages.
* Undoing a page deletion restores page content, annotations and outline
  entries, but links on *other* pages that pointed to the deleted page are not
  restored.
* Annotation types without an editor (file attachments, sound, widgets/form
  fields, 3D…) are displayed but read-only.
* Signatures are visual only (no certificates).
* Redaction leaves images and vector graphics under the box untouched, only
  covering them visually; it removes text.
