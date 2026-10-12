"""fileio.py - Textual-free file-reading helpers shared across modules.

Centralises the read-bytes / binary-detect / UTF-8-decode logic that several
modules used to re-implement with subtle differences (see issues.md). No
Textual dependency, so it can be imported by builder, diffing, app and
widgets without creating cycles.
"""

import os


def read_bytes(path):
    """Return (raw_bytes, error_or_None) for a path, or (None, msg) on OSError."""
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except OSError as exc:
        return None, str(exc)


def read_text_or_bytes(path):
    """Read a path, decoding as UTF-8 when it is text.

    Returns (content, is_binary, error_or_None):
      * content - str (UTF-8, errors="replace") for text files, bytes for
        binary files (contains a NUL byte), "" for an empty/missing path.
      * is_binary - True only when content is raw bytes.
      * error - a message when the file could not be read, else None.
    """
    if not path:
        return "", False, None
    raw, err = read_bytes(path)
    if err:
        return None, False, err
    if b"\x00" in raw:
        return raw, True, None
    return raw.decode("utf-8", "replace"), False, None
