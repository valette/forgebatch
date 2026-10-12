"""widgets.py - composite widgets used by the forgebatch app."""

import os

from rich.style import Style
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label, Static, TextArea
from textual.widgets._text_area import TextAreaTheme

from diffing import compute_patch, resolve_edit
from builder import KNOWN_TOKENS, TOKEN_RE, body_sweep_ranges, sweep_value_spans
from fileio import read_text_or_bytes

_FORGEBATCH_STYLES = {
    "forgebatch_token": Style(color="yellow", bold=True),
    "forgebatch_token_unknown": Style(color="red", bold=True),
    "forgebatch_sweep": Style(color="cyan", bold=True),
    "forgebatch_sweep_invalid": Style(color="red", bold=True),
}


def _register_theme(widget):
    """Register the shared forgebatch token/sweep theme on a mounted TextArea."""
    widget.register_theme(TextAreaTheme("forgebatch_tokens",
                                        syntax_styles=_FORGEBATCH_STYLES))
    widget.theme = "forgebatch_tokens"


class TokenTextArea(TextArea):
    """A TextArea that highlights {{TOKEN}} placeholders (e.g. {{JOBID}}).

    Known tokens ({{JOBID}}, {{JOBDIR}}) are highlighted in yellow; unknown
    tokens are highlighted in red so typos are obvious. Inline [[...]] sweep
    lists in the job body are highlighted in cyan when valid and red when the
    sweep syntax is invalid.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sweep_lines = {}

    def on_mount(self):
        _register_theme(self)
        self._refresh_sweeps()

    def _refresh_sweeps(self):
        lines = {}
        for (line, start, end, ok) in body_sweep_ranges(self.text):
            kind = "forgebatch_sweep" if ok else "forgebatch_sweep_invalid"
            lines.setdefault(line, []).append((start, end, kind))
        self._sweep_lines = lines

    def on_text_area_changed(self, event):
        self._refresh_sweeps()

    def _render_line(self, y):
        wrapped = self.wrapped_document
        try:
            line_index, _section = wrapped._offset_to_line_info[y + self.scroll_offset[1]]
        except IndexError:
            return super()._render_line(y)
        line = self.get_line(line_index)
        highlights = []
        for m in TOKEN_RE.finditer(line.plain):
            name = m.group(0)[2:-2]
            kind = "forgebatch_token" if name in KNOWN_TOKENS else "forgebatch_token_unknown"
            highlights.append((m.start(), m.end(), kind))
        highlights.extend(self._sweep_lines.get(line_index, []))
        self._highlights[line_index] = highlights
        return super()._render_line(y)


class SweepTextArea(TextArea):
    """A TextArea that highlights active sweep array values in a data file
    (the arrays that will be expanded by the Cartesian sweep)."""

    def __init__(self, *args, path="", **kwargs):
        super().__init__(*args, **kwargs)
        self._path = path
        self._sweep_lines = {}

    def set_path(self, path):
        self._path = path
        self._refresh_highlights()

    def _refresh_highlights(self):
        lines = {}
        for (line, start, end) in sweep_value_spans(self._path, self.text):
            lines.setdefault(line, []).append((start, end, "forgebatch_sweep"))
        self._sweep_lines = lines

    def on_mount(self):
        _register_theme(self)

    def on_text_area_changed(self, event):
        self._refresh_highlights()

    def _render_line(self, y):
        wrapped = self.wrapped_document
        try:
            line_index, _section = wrapped._offset_to_line_info[y + self.scroll_offset[1]]
        except IndexError:
            return super()._render_line(y)
        self._highlights[line_index] = self._sweep_lines.get(line_index, [])
        return super()._render_line(y)


class FilePane(Vertical):
    """One included item: a browse button, full-path input, a remove button,
    and (for files) an editable text area, or (for directories) a read-only
    listing plus a symlink/copy toggle."""

    def __init__(self, path="", file_edits=None, mode="symlink"):
        super().__init__()
        self.path = path
        self.file_edits = file_edits or {}
        self.mode = mode
        self.is_binary = False
        self._reload_timer = None
        self._source_path = None
        self._source_cache = None

    def compose(self):
        with Horizontal(id="file_pane_header"):
            yield Button("Browse", id="browse_file", tooltip="Choose a file or directory to include.")
            yield Input(self.path, placeholder="path to file or directory...", id="file_path", tooltip="Full path to the included item; files may be edited (copy only), directories are copied or symlinked.")
            yield Button("Remove", id="remove_file", tooltip="Remove this included item.")
            yield Checkbox("auto-wrap", id="file_wrap", value=True, tooltip="Toggle automatic line wrapping of this file.")
        yield Checkbox("symlink (save space)", id="symlink", value=(self.mode == "symlink"), tooltip="Copy this item into each job dir, or symlink it to save space (editing/sweeping is disabled for symlinked files).")
        yield Label("", id="file_error")
        with Horizontal(id="file_split"):
            yield SweepTextArea("", id="file_content", tooltip="Edit this file's copy (source file on disk is never modified).")
            yield Static("", id="diff_view", markup=False)
        with Horizontal(id="file_diff_bar"):
            yield Checkbox("show diff", id="show_diff", value=False, tooltip="Show the unified diff (source vs. your edits) side-by-side with the editor.")
            yield Static("", id="diff_spacer")
            yield Button("Clear modifications", id="clear_diff", tooltip="Discard your edits to this file and restore its on-disk content.")
        yield Static("", id="readonly", markup=False)

    def on_mount(self):
        self._reload_content()

    def on_unmount(self):
        if self._reload_timer is not None:
            self._reload_timer.stop()
            self._reload_timer = None

    def _is_dir(self):
        return bool(self.path) and os.path.isdir(self.path)

    def _listing(self):
        if not self.path:
            return ""
        try:
            entries = sorted(os.listdir(self.path))
        except OSError:
            return "(unreadable directory)"
        if not entries:
            return "(empty directory)"
        lines = []
        for name in entries:
            if os.path.isdir(os.path.join(self.path, name)):
                lines.append(name + "/")
            else:
                lines.append(name)
        return "\n".join(lines)

    def _content_and_error(self):
        if not self.path:
            return "", ""
        if self.path in self.file_edits:
            text, is_binary, conflicts = resolve_edit(self.path, self.file_edits[self.path])
            if is_binary:
                return "(binary file)", ""
            return text, ("unresolved edit conflicts" if conflicts else "")
        if not os.path.exists(self.path):
            return "", f"File not found: {self.path}"
        content, is_binary, err = read_text_or_bytes(self.path)
        if err:
            return "", f"Cannot read file: {err}"
        if is_binary:
            self.is_binary = True
            return f"(binary file, {len(content)} bytes)", ""
        return content, ""

    def _set_readonly_display(self, readonly):
        self.query_one("#file_content").display = not readonly
        self.query_one("#file_wrap").display = not readonly
        self.query_one("#file_split").display = not readonly
        self.query_one("#file_diff_bar").display = not readonly
        self.query_one("#show_diff").display = not readonly
        self.query_one("#symlink").display = True
        self.query_one("#readonly").display = readonly

    def _reload_content(self):
        self.path = self.get_path()
        is_dir = self._is_dir()
        is_link = self.mode == "symlink"
        if is_dir:
            self._set_readonly_display(True)
            self.query_one("#readonly", Static).update(self._listing())
            self.query_one("#file_error", Label).update("")
            return
        if is_link:
            self._set_readonly_display(True)
            self.query_one("#readonly", Static).update(
                "(symlinked \u2014 editing disabled)\n\n" + self.path)
            self.query_one("#file_error", Label).update("")
            return
        content, error = self._content_and_error()
        self.is_binary = isinstance(content, bytes) or content.startswith("(binary file")
        area = self.query_one("#file_content", SweepTextArea)
        area.load_text(content if isinstance(content, str) else "")
        area.set_path(self.path)
        area.read_only = self.is_binary
        if self.is_binary:
            self._set_readonly_display(True)
            self.query_one("#readonly", Static).update(
                "(binary file \u2014 editing disabled)\n\n" + self.path)
            self.query_one("#file_error", Label).update(error)
            return
        self._set_readonly_display(False)
        self.query_one("#diff_view").display = self.query_one("#show_diff", Checkbox).value
        self.query_one("#file_error", Label).update(error)
        self._update_diff()

    def on_input_changed(self, event):
        if event.input.id == "file_path":
            self._schedule_reload()

    def _schedule_reload(self):
        if self._reload_timer is not None:
            self._reload_timer.stop()
        self._reload_timer = self.set_timer(1.0, self._reload_content)

    def get_path(self):
        return self.query_one("#file_path", Input).value.strip()

    def get_content(self):
        return self.query_one("#file_content", TextArea).text

    def get_kind(self):
        return "dir" if self._is_dir() else "file"

    def get_mode(self):
        return "symlink" if self.query_one("#symlink", Checkbox).value else "copy"

    def _source_text(self):
        """The on-disk source content for the diff, cached per path so we don't
        re-read the file on every keystroke."""
        if self._source_path != self.path or self._source_cache is None:
            content, _is_binary, err = read_text_or_bytes(self.path)
            self._source_cache = content if (not err and not _is_binary) else ""
            self._source_path = self.path
        return self._source_cache

    def _update_diff(self):
        if (not self.path or self._is_dir()
                or not self.query_one("#show_diff", Checkbox).value):
            return
        diff = compute_patch(self._source_text(), self.get_content())
        self.query_one("#diff_view", Static).update(diff or "(no changes)")

    def on_checkbox_changed(self, event):
        if event.checkbox.id == "file_wrap":
            self.query_one("#file_content", TextArea).soft_wrap = event.checkbox.value
        elif event.checkbox.id == "show_diff":
            self.query_one("#diff_view").display = event.checkbox.value
            if event.checkbox.value:
                self._update_diff()
        elif event.checkbox.id == "symlink":
            self.mode = "symlink" if event.checkbox.value else "copy"
            self._reload_content()

    def on_text_area_changed(self, event):
        if event.text_area.id == "file_content":
            self._update_diff()
