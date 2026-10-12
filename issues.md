# Issues

Tracked bugs found during deep code review. Each is fixed with its own commit;
tick the checkbox as it is resolved. Severity: **H**igh / **M**edium / **L**ow.

## Open / fixed

- [x] **H — Removing a file tab reverts every other tab's copy/symlink mode**
  `app.py:_remove` / `_collect_paths`. `_collect_paths` returns bare path
  strings, discarding each pane's `mode`, so `_rebuild_rows` recreates the
  remaining panes with their defaults (files → copy, dirs → symlink). Removing
  one item silently resets the mode of every other item.
- [x] **M — Included files with identical basenames silently collide**
  `builder.py:build_jobs` keys `variant["files"]` by `os.path.basename(path)`;
  two files with the same name from different directories overwrite each other
  (only the last survives) with no warning and only one appears in the Preview
  tree — silent data loss in the generated jobs.
- [x] **M — Diff conflict path duplicates the whole file after the markers**
  `diffing.py:apply_patch`. When a hunk can't be placed, `_conflict_block` is
  appended but `pos` is not advanced, so the trailing `if pos < len(lines)`
  re-appends the entire remaining base text — the resolved text contains the
  conflict markers plus a full duplicate of the original file.
- [x] **M — Per-keystroke disk reads and full-file parses on the UI thread**
  `widgets.py:_update_diff` reads the source file from disk on every keystroke
  even when the diff view is hidden; `app.py:_update_token_warning` re-parses
  every included data file on every keystroke. Jank/freezes on large files.
- [x] **L — "duplicate included file path(s)" false positive for empty tabs**
  `app.py:_update_token_warning` counts empty pane paths; two freshly added
  empty tabs trigger a spurious duplicate-path warning.
- [x] **L — Empty sweep array silently generates zero jobs**
  `builder.py:collect_dims`/`build_jobs`. An empty array on a swept key becomes
  a dimension with no values, collapsing the Cartesian product to zero jobs with
  no error or warning ("0 job(s) generated").
- [x] **L — Pending reload timer fires after its pane is removed**
  `widgets.py:_schedule_reload`. The 1s debounce timer isn't cancelled on pane
  removal, so it can fire `_reload_content` on a detached/unmounted pane and
  raise inside the timer callback.
- [x] **M — Edits to files without a trailing newline always conflict**
  `diffing.py:compute_patch`. `difflib.unified_diff` emits `-x`/`+y` records
  without trailing newlines, so for a file whose last line has no newline
  (e.g. `json.dump` output) the records ran together and the patch could never
  be re-applied — the edit always produced conflict markers in the generated
  job. Normalize both sides to newline-terminated lines.
