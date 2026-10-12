"""diffing.py - store/replay edits as git-style unified diffs.

Pure stdlib, no Textual dependency, so it can be imported by both `builder`
and `app` without creating circular imports. Edits are persisted as unified
diff strings (like `git diff`), which lets us re-apply them to the *current*
source file content rather than storing a stale full snapshot.

An edit value in the config is either:
  * a unified diff string (new format), or
  * a legacy full-text override (old configs) -- detected by `is_patch`.
"""

import difflib
import os

from fileio import read_bytes

_CONFLICT_START = "<<<<<<< forgebatch edit"
_CONFLICT_MID = "======="
_CONFLICT_END = ">>>>>>> source file"


def _norm_lines(text):
    """Split text into lines for unified diffing. A final line without a
    trailing newline is treated as newline-terminated so that difflib emits each
    diff record on its own line (otherwise the last '-x' and '+y' records run
    together and the patch cannot be re-applied)."""
    if not text:
        return []
    if not text.endswith("\n"):
        text += "\n"
    return text.splitlines(keepends=True)


def compute_patch(base_text, edited_text):
    """Return a unified diff string transforming base_text into edited_text.

    Both args are str. An empty/missing base yields an all-addition patch so
    that an edit can be replayed even when the source file does not exist.
    """
    base_lines = _norm_lines(base_text or "")
    edit_lines = _norm_lines(edited_text or "")
    diff = difflib.unified_diff(
        base_lines,
        edit_lines,
        fromfile="a/source",
        tofile="b/edit",
    )
    patch = "".join(diff)
    if not patch:
        return ""
    return patch


def is_patch(value):
    """Heuristic: does this stored value look like a unified diff vs. legacy text?"""
    if not isinstance(value, str):
        return False
    return value.startswith("--- ") and "@@ " in value


def apply_patch(base_text, patch):
    """Apply a unified diff to base_text, merging what is possible.

    Returns (text, conflicts) where conflicts is a bool indicating whether any
    hunk could not be cleanly placed. Unplaceable hunks are emitted inline with
    conflict markers so the user can resolve them in the editable region.
    """
    if not patch:
        return base_text or "", False
    lines = _norm_lines(base_text or "")
    hunks = _parse_patch(patch)
    conflicts = False
    out = []
    pos = 0
    for hunk in hunks:
        start_line = hunk["start"]
        # ensure we don't lose lines before this hunk
        if start_line > pos:
            out.extend(lines[pos:start_line])
            pos = start_line
        result, consumed, matched = _apply_hunk(lines, pos, hunk)
        if matched:
            out.extend(result)
            pos = consumed
        else:
            conflicts = True
            region = lines[pos:pos + len(hunk["del"])]
            out.extend(_conflict_block(hunk, region))
            pos += len(hunk["del"])
    if pos < len(lines):
        out.extend(lines[pos:])
    return "".join(out), conflicts


def _parse_patch(patch):
    """Parse a unified diff into a list of hunk dicts."""
    hunks = []
    cur = None
    for raw in patch.splitlines(keepends=True):
        line = raw
        if line.startswith("@@ "):
            if cur is not None:
                hunks.append(cur)
            meta = line.strip()
            # @@ -a,b +c,d @@
            parts = meta.split()
            old = parts[1][1:]  # strip leading '-'
            if "," in old:
                start_str, _count = old.split(",", 1)
            else:
                start_str = old
            start = int(start_str) if start_str != "0" else 0
            cur = {"start": start - 1 if start > 0 else 0,
                   "add": [], "del": [], "old_count": 0, "new_count": 0}
        elif cur is not None:
            if line.startswith("+"):
                cur["add"].append(line[1:])
            elif line.startswith("-"):
                cur["del"].append(line[1:])
            elif line.startswith(" "):
                content = line[1:]
                cur["add"].append(content)
                cur["del"].append(content)
    if cur is not None:
        hunks.append(cur)
    return hunks


def _apply_hunk(lines, pos, hunk):
    """Try to place a hunk at/after pos. Returns (result_lines, new_pos, matched)."""
    del_lines = hunk["del"]
    add_lines = hunk["add"]
    # Try exact match of the deletion/context block at the current position.
    if lines[pos:pos + len(del_lines)] == del_lines:
        return add_lines, pos + len(del_lines), True
    # Otherwise search forward for the block.
    for i in range(pos, len(lines) - len(del_lines) + 1):
        if lines[i:i + len(del_lines)] == del_lines:
            result = lines[pos:i] + add_lines
            return result, i + len(del_lines), True
    return [], pos, False


def _conflict_block(hunk, region=None):
    """Emit conflict markers around the un-placeable hunk. `region` is the
    current base lines the hunk covers (shown under the conflict start marker),
    so the real file content is not lost nor duplicated when apply_patch
    advances past the hunk."""
    block = [_CONFLICT_START + "\n"]
    block.extend(region if region is not None else hunk["del"])
    block.append(_CONFLICT_MID + "\n")
    block.extend(hunk["add"])
    block.append(_CONFLICT_END + "\n")
    return block


def resolve_edit(path, value):
    """Return (text, is_binary, conflicts) for a stored edit value.

    Reads the *current* file content (if present) and applies the stored patch,
    or returns a legacy full-text override directly.
    """
    if value is None:
        return None, False, False
    if not is_patch(value):
        # legacy full-text override
        return value, False, False
    base_text = ""
    if path and os.path.isfile(path):
        raw, _err = read_bytes(path)
        if raw is None:
            raw = b""
        if b"\x00" in raw:
            return raw, True, False
        base_text = raw.decode("utf-8", "replace")
    text, conflicts = apply_patch(base_text, value)
    return text, False, conflicts
