"""screens.py - modal dialog screens for forgebatch (browse, editor, prompt, presets)."""

import os
import time
from pathlib import Path

from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from config import read_config, write_config

# Shared chrome for every dialog panel. Each dialog concatenates this with its
# own size overrides into a single CSS block: Textual's CSS-inheritance merge
# of the panel rules across a base class and a subclass breaks `height: auto`
# (and centering via a base type selector does not propagate to subclasses), so
# centering is applied programmatically and each dialog owns one self-contained
# block.
_MODAL_CHROME = """
#modal_panel {
    border: round $primary;
    background: $panel;
    padding: 1 2;
}
#modal_title { text-style: bold; height: auto; margin: 0 0 1 0; }
#modal_body { height: 1fr; overflow-y: auto; }
#modal_footer { height: auto; align: center top; margin: 1 0 0 0; }
#modal_footer Button { margin: 0 1 0 0; }
"""


class PanelModal(ModalScreen):
    """Base for all forgebatch dialogs: a centered, bordered panel with a bold
    title header, a scrollable content body, and a footer button row.

    Subclasses implement body() and footer() and set their own `CSS` (the
    shared `_MODAL_CHROME` plus per-dialog size overrides). Centering is done
    programmatically in __init__ because a base type selector does not cascade
    to subclasses."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title="", **kwargs):
        super().__init__(**kwargs)
        self.modal_title = title
        self.styles.align = ("center", "middle")

    def compose(self):
        with Vertical(id="modal_panel"):
            yield Label(self.modal_title, id="modal_title")
            with VerticalScroll(id="modal_body"):
                yield from self.body()
            with Horizontal(id="modal_footer"):
                yield from self.footer()

    def body(self):
        yield Static("")

    def footer(self):
        yield Button("OK", id="ok_btn")
        yield Button("Cancel", id="cancel_btn")

    def action_cancel(self):
        self.dismiss(None)


class BrowseTree(DirectoryTree):
    """DirectoryTree whose Enter chooses the selected file. The stock Tree binds
    Enter to `select_cursor`, which swallows the key and never imports the
    selected file; we redirect Enter to post a FileChosen message so the browser
    selects the cursor node (falls back to the current directory like Choose)."""

    BINDINGS = [
        *[b for b in DirectoryTree.BINDINGS if b.key != "enter"],
        Binding("enter", "choose_file", "Choose"),
    ]

    class FileChosen(Message):
        """Posted when Enter is pressed while the tree has focus."""

    def __init__(self, path, *, filter_ext=None, **kwargs):
        super().__init__(path, **kwargs)
        self.filter_ext = filter_ext

    def filter_paths(self, paths):
        if not self.filter_ext:
            return paths
        fext = self.filter_ext.lower()
        return (p for p in paths if p.is_dir() or p.name.lower().endswith(fext))

    def action_choose_file(self):
        self.post_message(self.FileChosen())


class FileBrowserScreen(PanelModal):
    """Full-screen directory browser used by the Browse buttons."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "choose", "Choose"),
    ]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: 88%; height: 88%; }
    #dirtree { height: 1fr; }
    """

    def __init__(self, start_path, filter_ext=None):
        super().__init__(title="Browse")
        self.start_path = start_path or os.getcwd()
        self.filter_ext = filter_ext
        self.dirtree = None
        self._last_click = None

    def _acceptable(self, path):
        if not self.filter_ext:
            return True
        return path.lower().endswith(self.filter_ext.lower())

    def body(self):
        yield Input(placeholder="enter a path...", value=self.start_path, id="browse_path", tooltip="Type a path and press Enter; Enter on a file selects it.")
        tree = BrowseTree(self._start_dir(), filter_ext=self.filter_ext, id="dirtree")
        tree.tooltip = "Navigate directories; Enter or double-click a file to select it."
        yield tree

    def footer(self):
        yield Button("Choose", id="choose_btn", tooltip="Select the highlighted file or current directory.")
        yield Button("Cancel", id="cancel_btn", tooltip="Cancel.")

    def _start_dir(self):
        d = self.start_path
        if d and os.path.isdir(d):
            return d
        parent = os.path.dirname(d) if d else ""
        if parent and os.path.isdir(parent):
            return parent
        return os.getcwd()

    def on_mount(self):
        self.dirtree = self.query_one("#dirtree", DirectoryTree)
        self.dirtree.path = Path(self._start_dir())
        self.dirtree.refresh()
        self.query_one("#browse_path", Input).focus()

    def on_input_submitted(self, event):
        if event.input.id == "browse_path":
            self._go(event.input.value.strip())

    def _go(self, p):
        if os.path.isdir(p):
            self.dirtree.path = Path(p)
            self.dirtree.refresh()
        elif os.path.isfile(p):
            if self._acceptable(p):
                self.dismiss(p)

    def action_cancel(self):
        self.dismiss(None)

    def on_browse_tree_file_chosen(self, event):
        self.action_choose()

    def action_choose(self):
        node = self.dirtree.cursor_node
        data = node.data if node is not None else None
        if data is not None and (data.path.is_dir() or self._acceptable(str(data.path))):
            self.dismiss(str(data.path))
        else:
            self.dismiss(str(self.dirtree.path))

    def on_button_pressed(self, event):
        if event.button.id == "choose_btn":
            self.action_choose()
        elif event.button.id == "cancel_btn":
            self.action_cancel()

    def _clicked_node(self, event):        # Resolve the node a click refers to. Textual's Tree moves its cursor on
        # the first click, so fall back to the cursor node when the click has no
        # line meta or the line maps to no node.
        node = None
        line = event.style.meta.get("line")
        if line is not None:
            node = self.dirtree.get_node_at_line(line)
        if node is None:
            node = self.dirtree.cursor_node
        return node

    def on_click(self, event):
        # Double-click a file to select it. Textual's Click.chain only increments
        # when two clicks land on the exact same pixel, so a real double-click
        # with any mouse movement resets chain to 1. Detect a double-click both
        # from chain and from a second click on the same file within the chain
        # time window, so it works regardless of tiny pointer drift.
        node = self._clicked_node(event)
        if node is None or node.data is None:
            return
        path = str(node.data.path)
        now = time.monotonic()
        prev = self._last_click
        self._last_click = (path, now)
        if not self._acceptable(path):
            return
        is_double = event.chain >= 2 or (
            prev is not None and prev[0] == path and (now - prev[1]) <= 0.5
        )
        if is_double:
            self.dismiss(path)


class PromptScreen(PanelModal):
    """Generic prompt with a text input and OK/Cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("enter", "ok", "OK")]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: auto; height: auto; max-width: 72; }
    #modal_body { height: auto; }
    """

    def __init__(self, title="", prompt="", value=""):
        super().__init__(title=title)
        self.prompt_text = prompt
        self.value = value

    def body(self):
        yield Label(self.prompt_text, id="prompt_text")
        yield Input(value=self.value, id="prompt_input", tooltip="Enter a value; Enter confirms.")

    def on_mount(self):
        self.query_one("#prompt_input", Input).focus()

    def action_ok(self):
        self.dismiss(self.query_one("#prompt_input", Input).value.strip())

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id == "ok_btn":
            self.action_ok()
        elif event.button.id == "cancel_btn":
            self.action_cancel()


class TextInputScreen(PanelModal):
    """Dialog with a multi-line text area for pasting a whole job as text.

    No Enter->OK binding so Enter can still insert newlines; OK/Cancel buttons
    confirm or abort (Escape also cancels)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: 72%; height: 65%; }
    #text_area { height: 1fr; }
    """

    def __init__(self, title="", tooltip="", hint=""):
        super().__init__(title=title)
        self.screen_tooltip = tooltip
        self.screen_hint = hint

    def body(self):
        if self.screen_hint:
            yield Label(self.screen_hint, id="text_hint")
        yield TextArea("", id="text_area", tooltip=self.screen_tooltip)

    def on_mount(self):
        self.query_one("#text_area", TextArea).focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id == "ok_btn":
            self.dismiss(self.query_one("#text_area", TextArea).text)
        elif event.button.id == "cancel_btn":
            self.dismiss(None)


class PresetOptionList(OptionList):
    """Selectable preset list. Single click / arrow selection fires the default
    OptionSelected/OptionHighlighted (used to "select" the name); double-click
    loads the preset via a PresetChosen message. Enter is handled at the screen
    level (priority binding) so it loads whether the list or the name input is
    focused."""

    class PresetChosen(Message):
        """Posted when a preset is double-clicked to load it."""

        def __init__(self, name):
            super().__init__()
            self.name = name

    async def _on_click(self, event):
        await super()._on_click(event)
        if getattr(event, "chain", 0) >= 2:
            h = self.highlighted
            if h is not None and not self._options[h].disabled:
                self.post_message(self.PresetChosen(self._options[h].prompt))


class PresetsScreen(PanelModal):
    """Load/save/delete presets stored in the config."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "load_selected", "Load", priority=True),
    ]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: 60%; height: 50%; }
    #preset_list { height: 1fr; }
    """

    def __init__(self, main_app, current):
        super().__init__(title="Presets")
        self.main = main_app
        self.current = current

    def body(self):
        yield Input(placeholder="preset name...", id="preset_name", tooltip="Name of the preset to save, load, or delete.")
        yield PresetOptionList(id="preset_list")

    def footer(self):
        yield Button("Save as preset", id="save_preset", tooltip="Save current settings under the typed name.")
        yield Button("Load selected", id="load_preset", tooltip="Apply the named preset to the app.")
        yield Button("Delete selected", id="del_preset", tooltip="Delete the named preset.")
        yield Button("Close", id="close_presets", tooltip="Close the presets dialog.")

    def on_mount(self):
        self._refresh_list()
        self.query_one("#preset_list").focus()

    def action_cancel(self):
        self.dismiss(None)

    def action_load_selected(self):
        self._load(self._selected_name())

    def _names(self):
        cfg = read_config()
        return sorted(cfg["presets"].keys())

    def _refresh_list(self):
        names = self._names()
        self.query_one("#preset_list", OptionList).clear_options()
        if not names:
            self.query_one("#preset_list", OptionList).add_option(Option("(no presets)", disabled=True))
        else:
            self.query_one("#preset_list", OptionList).add_options([Option(n, id=n) for n in names])

    def _selected_name(self):
        """Name of the currently selected preset: the highlighted option if any,
        else the typed name."""
        ol = self.query_one("#preset_list", OptionList)
        h = ol.highlighted
        if h is not None and not ol.get_option_at_index(h).disabled:
            return ol.get_option_at_index(h).prompt
        return self.query_one("#preset_name", Input).value.strip()

    def _load(self, name):
        cfg = read_config()
        if name and name in cfg["presets"]:
            self.main.apply_preset(dict(cfg["presets"][name]))
            self.main._set_status(f"Loaded preset '{name}'")
            self.dismiss(None)

    def on_option_list_option_highlighted(self, event):
        if event.option and not event.option.disabled:
            self.query_one("#preset_name", Input).value = str(event.option.prompt)

    def on_preset_option_list_preset_chosen(self, event):
        self._load(event.name)

    def on_button_pressed(self, event):
        bid = event.button.id
        if bid == "close_presets":
            self.dismiss(None)
            return
        name = self.query_one("#preset_name", Input).value.strip()
        cfg = read_config()
        if bid == "save_preset" and name:
            cfg["presets"][name] = dict(self.current)
            self._persist(cfg)
        elif bid == "load_preset":
            self._load(self._selected_name())
        elif bid == "del_preset" and name:
            cfg["presets"].pop(name, None)
            self._persist(cfg)

    def _persist(self, cfg):
        """Write presets to disk and keep the app's in-memory cfg in sync, so a
        later save/autosave cannot overwrite disk with a stale presets dict."""
        write_config(cfg)
        self.main.cfg["presets"] = cfg["presets"]
        self._refresh_list()


def format_submit_log(log):
    """Render the submission log (list of per-job dicts) as readable text.
    Used for both the log dialog and the on-disk submit.log."""
    lines = []
    ok = sum(1 for e in log if e.get("ok"))
    failed = len(log) - ok
    lines.append(f"# Submission log - {ok} submitted, {failed} failed")
    lines.append("")
    for i, e in enumerate(log, start=1):
        status = "OK" if e.get("ok") else "FAIL"
        lines.append(f"## Job {i} - {status}  {e.get('label', '')}")
        lines.append("")
        lines.append(f"- file: `{e.get('job_file', '')}`")
        cmd = " ".join(e.get("cmd") or [])
        if cmd:
            lines.append(f"- command: `{cmd}`")
        rc = e.get("returncode")
        lines.append(f"- exit code: {rc if rc is not None else 'n/a'}")
        if e.get("error"):
            lines.append(f"- error: `{e['error']}`")
        lines.append("")
        for name in ("stdout", "stderr"):
            txt = (e.get(name) or "").strip()
            if txt:
                lines.append(f"### {name}")
                lines.append("")
                lines.append("```")
                lines.extend(txt.splitlines())
                lines.append("```")
                lines.append("")
    return "\n".join(lines)


class SubmitLogScreen(PanelModal):
    """Dialog showing the per-job submission log. Closes with the Close
    button or Escape."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: 90%; height: 90%; }
    """

    def __init__(self, log):
        super().__init__(title="Submission log")
        self._log = list(log)

    def body(self):
        yield Markdown(format_submit_log(self._log))

    def footer(self):
        yield Button("Close", id="close_log", tooltip="Close (or press Escape).")

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id == "close_log":
            self.dismiss(None)


class JobDetailsScreen(PanelModal):
    """Dialog showing full scheduler output for one job (scontrol show job /
    qstat -f). Read-only; closes with Close or Escape."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: 90%; height: 80%; }
    """

    def __init__(self, job_id, detail_text):
        super().__init__(title=f"Job details: {job_id}")
        self._detail = detail_text or "(no details)"

    def body(self):
        yield Static(self._detail, id="details_text", markup=False)

    def footer(self):
        yield Button("Close", id="close_details", tooltip="Close (or press Escape).")

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id == "close_details":
            self.dismiss(None)


class ConfirmScreen(PanelModal):
    """Generic confirmation dialog (OK / Cancel). Dismisses with True on OK,
    None on Cancel/Escape."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("enter", "ok", "OK")]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: auto; height: auto; max-width: 72; }
    #modal_body { height: auto; }
    """

    def __init__(self, title="", prompt=""):
        super().__init__(title=title)
        self.prompt_text = prompt

    def body(self):
        yield Label(self.prompt_text, id="confirm_text")

    def footer(self):
        yield Button("OK", id="ok_btn", tooltip="Confirm.")
        yield Button("Cancel", id="cancel_btn", tooltip="Cancel (or press Escape).")

    def action_ok(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id == "ok_btn":
            self.action_ok()
        elif event.button.id == "cancel_btn":
            self.action_cancel()


class HelpScreen(PanelModal):
    """Dialog showing extensive help about forgebatch. Closes with the
    Close button or Escape (no other shortcuts)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = _MODAL_CHROME + """
    #modal_panel { width: 82%; height: 82%; }
    """

    def __init__(self):
        super().__init__(title="Help")

    def body(self):
        yield Markdown(_load_help())

    def footer(self):
        yield Button("Close", id="close_help", tooltip="Close help (or press Escape).")

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id == "close_help":
            self.dismiss(None)


def _load_help():
    """Return the help Markdown for the HelpScreen, read from the
    help.md file that sits next to this module. Falls back to a short
    message if the file cannot be read so the app never crashes."""
    path = Path(os.path.dirname(os.path.realpath(__file__))) / "help.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "# Help\nHelp file `help.md` could not be loaded."
