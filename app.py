"""app.py - the forgebatch Textual application (main tabbed UI)."""

from pathlib import Path

import asyncio
import getpass
import os
import re
import shutil

from rich.text import Text

from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, Checkbox, DataTable, Header, Input, Label,
                             Select, Static, TextArea, Tree)

from builder import build_jobs, existing_targets, job_header, parse_import, unknown_tokens, validate_data
from builder import body_sweep_errors
import config
from config import default_settings, scheduler, use_slurm, read_config, write_config
import diffing
from fileio import read_text_or_bytes
from runner import (cancel_cmd, detail_cmd, parse_running_output, progress_cells,
                    run_submission, state_category)
from screens import (ConfirmScreen, FileBrowserScreen, HelpScreen, JobDetailsScreen,
                     PromptScreen, PresetsScreen, SubmitLogScreen, TextInputScreen,
                     format_submit_log)
from widgets import FilePane, TokenTextArea

MAIL_TYPE_OPTIONS = ("ALL", "NONE", "BEGIN", "END", "FAIL", "REQUEUE", "TIME_LIMIT")


def _read_text(path):
    """Read a UTF-8 text file; return '' on any error or if missing/binary."""
    content, is_binary, _err = read_text_or_bytes(path)
    if content is None or is_binary:
        return ""
    return content


class ForgeBatchApp(App):
    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+x", "quit", "Quit"),
        Binding("ctrl+s", "save", "Save"),
    ]

    CSS = """
    #actions { height: 3; padding: 0 1; align: left middle; overflow-x: auto; }
    #actions Button, #tab_bar Button { min-width: 0; padding: 0 1; margin: 0 1 0 0; }
    #scheduler_info { width: auto; padding: 0 1; color: $text-muted; height: 100%; content-align: center middle; }
    #actions_spacer { width: 1fr; }
    #tab_bar { height: 3; padding: 0 1; align: left middle; background: $panel; overflow-x: auto; }
    #tab_bar Button { margin: 0 1 0 0; max-width: 24; overflow: hidden; text-overflow: ellipsis; }
    #file_tabs { width: auto; overflow-x: auto; }
    #tab_bar Button.active { background: $accent; color: $text; }
    #tab_spacer { width: 1fr; }
    #tab_preview { margin: 0 0 0 1; }
    #tab_jobs, #tab_preview { max-width: 18; overflow: hidden; text-overflow: ellipsis; }
    #tab_jobs.active, #tab_preview.active { background: $accent; color: $text; }
    #pane_settings { height: 1fr; overflow-y: auto; }
    #settings_grid { height: auto; padding: 1 0 1 2; }
    #settings_grid .srow { height: 3; align: left middle; }
    #settings_grid .srow Label { width: 20; height: 100%; content-align: center middle; }
    #settings_grid .srow Input { width: 1fr; margin: 0 0 0 1; }
    #settings_grid .srow #single_job { min-width: 0; margin: 0 0 0 1; }
    #settings_grid #job_root.invalid { border: round $error; }
    #settings_grid .srow Button { width: auto; min-width: 8; margin: 0 0 0 1; }
    #pane_settings > .srow { height: 3; align: left middle; margin: 1 2 0 2; }
    #pane_settings > .srow Label { width: 20; height: 100%; content-align: center middle; }
    #pane_settings > .srow Input { width: 1fr; margin: 0 0 0 1; }
    #pane_settings > .srow Button { width: auto; min-width: 8; margin: 0 0 0 1; }
    #pane_settings > .srow #import_job,
    #pane_settings > .srow #import_text,
    #pane_settings > .srow #clear_job { width: 1fr; margin: 0 1 0 0; }
    #job_header_view { height: auto; min-height: 3; max-height: 8; margin: 1 2 0 2; border: round $primary; }
    #job_body_editor { height: 1fr; min-height: 6; margin: 0 2 1 2; border: round $primary; }
    .job_pane_label { padding: 1 2 0 2; color: $text-muted; }
    .job_pane_label_row { padding: 1 2 0 2; height: auto; align: left middle; }
    .job_pane_label_row Label { color: $text-muted; width: 1fr; }
    .job_pane_label_row Checkbox { color: $text; margin: 0 2 0 0; }
    #pane_files { height: 1fr; padding: 1 2; }
    #pane_running { height: 1fr; padding: 1 2; }
    #running_header { height: auto; align: left middle; }
    #running_header Label { width: 1fr; height: 100%; content-align: center middle; }
    #running_header Button { margin: 0 0 0 1; }
    #running_only_user { margin: 0 0 0 1; }
    #running_controls { height: auto; align: left middle; margin: 0 0 1 0; }
    #running_controls Input { width: 18; margin: 0 1 0 0; }
    #running_controls Select { width: 12; margin: 0 1 0 0; }
    #running_controls Button { margin: 0 0 0 1; }
    #running_spacer { width: 1fr; }
    #running_list { height: 1fr; border: round $primary; padding: 0 1; }
    #file_pane_header { height: 3; align: left middle; }
    #file_pane_header Input { width: 1fr; margin: 0 1 0 0; }
    #file_pane_header Button { margin: 0 0 0 1; }
    #file_wrap { margin: 0 0 0 1; }
    #browse_file { margin: 0 1 0 0; }
    #file_error { color: $error; padding: 0 0 0 2; }
    #file_content { height: 1fr; margin: 1 0 0 0; }
    #file_diff_bar { height: 3; align: left middle; }
    #file_diff_bar #show_diff { margin: 0 0 0 2; }
    #diff_spacer { width: 1fr; }
    #status_bar { height: 3; padding: 0 2; content-align: left middle; color: $text; }
    #status_bar.warn { color: $error; }
    #preview_split { height: 1fr; }
    #preview_conflict_bar { height: auto; max-height: 6; padding: 0 1; align: left top; }
    #preview_conflicts { width: 1fr; min-width: 0; max-height: 6; overflow-y: auto; overflow-x: auto; }
    #preview_conflicts.conflict { color: $error; text-style: bold; }
    #override_conflicts { margin: 0 0 0 1; }
    #job_tree { width: 45%; border: round $primary; }
    #job_viewer { border: round $primary; }
    #preview_actions { height: 3; padding: 0 1; align: left middle; }
    #preview_actions Button { margin: 0 1 0 0; }
    #preview_actions Input { width: 14; margin: 0 0 0 1; }
    #preview_actions Label { height: 100%; content-align: left middle; margin: 0 1 0 0; }
    #preview_spacer { width: 1fr; }
    """

    def __init__(self, preset=None):
        super().__init__()
        self.preset_name = preset
        self.cfg = read_config()
        self.settings = dict(self.cfg.get("last") or default_settings())
        self.apply_settings(self.settings)
        self._autosave_timer = None
        self._status_is_warning = False
        self._data_validation_cache = {}
        self._override_conflicts = False

    # ---- settings <-> UI helpers ----

    def collect_settings(self):
        self.settings["job_root"] = self.query_one("#job_root", Input).value.strip()
        self.settings["job_name"] = self.query_one("#job_name", Input).value.strip()
        self.settings["single_job"] = self.query_one("#single_job", Checkbox).value
        self.settings["email"] = self.query_one("#email", Input).value.strip()
        self.settings["parallelism_limit"] = self.query_one("#parallel", Input).value.strip()
        self.settings["included_files"], self.settings["included_dirs"] = \
            self._collect_rows()
        self.settings["file_edits"] = self.file_edits
        self.settings["job_body"] = self.query_one("#job_body_editor", TextArea).text
        self.settings["mail_type"] = self._collect_mail_type()

    def _collect_mail_type(self):
        if not use_slurm:
            return []
        text = self.query_one("#mail_type_input", Input).value
        return [t.strip() for t in text.split(",") if t.strip()]

    def apply_settings(self, s):
        """Apply a settings dict to the UI. Migrates legacy `user` -> email."""
        merged = dict(default_settings())
        merged.update(s)
        # legacy migration: old configs stored `user`, used only for email
        if "email" not in s and "user" in s:
            merged["email"] = s["user"]
        self.settings = merged
        self.file_edits = dict(merged.get("file_edits") or {})
        self._rows = []
        for item in merged.get("included_files") or []:
            if isinstance(item, dict):
                mode = (item.get("mode") if isinstance(item, dict) else "") or "copy"
                self._rows.append({"path": item.get("path", ""), "mode": mode})
            else:
                self._rows.append(str(item))
        for item in merged.get("included_dirs") or []:
            path = item.get("path") if isinstance(item, dict) else item
            mode = (item.get("mode") if isinstance(item, dict) else "") or "symlink"
            self._rows.append({"path": str(path), "mode": mode})

    def apply_preset(self, settings):
        """Apply a saved preset and refresh the whole UI to reflect it."""
        self.apply_settings(settings)
        self._set_settings_inputs(self.settings)
        self._refresh_job_header()
        self._load_job_body()
        self._update_token_warning()
        self.run_worker(self._rebuild_rows)

    def _set_settings_inputs(self, settings):
        self.query_one("#job_root", Input).value = settings.get("job_root", "")
        self.query_one("#job_name", Input).value = settings.get("job_name", "")
        self.query_one("#single_job", Checkbox).value = bool(settings.get("single_job"))
        self.query_one("#email", Input).value = settings.get("email", "")
        self.query_one("#parallel", Input).value = settings.get("parallelism_limit", "0")
        if use_slurm:
            self._set_mail_type(settings.get("mail_type") or ["ALL"])

    def _collect_rows(self):
        area = self.query_one("#pane_files", Vertical)
        files = []
        dirs = []
        seen = set()
        for pane in area.query(FilePane):
            path = pane.get_path()
            if pane.get_kind() == "dir":
                dirs.append({"path": path, "mode": pane.get_mode() or "symlink"})
                continue
            files.append({"path": path, "mode": pane.get_mode() or "copy"})
            if pane.get_mode() == "symlink":
                continue
            if not path or path in seen:
                continue
            seen.add(path)
            if not pane.is_binary and os.path.exists(path):
                patch = diffing.compute_patch(_read_text(path), pane.get_content())
                if patch:
                    self.file_edits[path] = patch
                else:
                    self.file_edits.pop(path, None)
        return files, dirs

    def _refresh_job_header(self):
        """Render the read-only scheduler header into the job tab."""
        root = (self.settings.get("job_root") or "").strip()
        single = bool(self.settings.get("single_job"))
        job_path = root if single else (os.path.join(root, "{{JOBID}}") if root else root)
        header = "\n".join(job_header(self.settings, "" if single else "{{JOBID}}", job_path))
        self.query_one("#job_header_view", TextArea).load_text(header)

    def _load_job_body(self):
        """Load the stored job body into the editable pane."""
        self.query_one("#job_body_editor", TextArea).load_text(
            self.settings.get("job_body") or "")

    def deliver_screenshot(self, filename=None, path=None, time_format=None):
        path = path or os.getcwd()
        os.makedirs(path, exist_ok=True)
        return super().deliver_screenshot(filename, path=path, time_format=time_format)

    # ---- compose / tabs ----

    def compose(self):
        yield Header()
        with Horizontal(id="actions"):
            yield Button("Help", id="help", tooltip="Show extensive help about what forgebatch can do.")
            yield Button("Presets", id="presets", tooltip="Choose a saved configuration preset.")
            yield Static("", id="actions_spacer")
            yield Static(id="scheduler_info")
            yield Button("Jobs queue", id="tab_jobs", tooltip="List the current user's active scheduler jobs.")
        with Horizontal(id="tab_bar"):
            yield Button("job.slurm" if use_slurm else "job.pbs", id="tab_settings", tooltip="Edit scheduler settings, the job body, and included data files.")
            yield Horizontal(id="file_tabs")
            yield Static("", id="tab_spacer")
            yield Button("Preview job(s)", id="tab_preview", tooltip="Preview generated job directories, then Write & Submit.")
        with Vertical(id="content"):
            with Vertical(id="pane_settings"):
                with Horizontal(classes="srow"):
                    yield Button("Import job from file", id="import_job", tooltip="Import an existing scheduler job file: populate the email and the job body (the original file is never modified).")
                    yield Button("Import job from text", id="import_text", tooltip="Paste a scheduler job as text to populate the email and the job body (the original is never modified).")
                    yield Button("Clear", id="clear_job", tooltip="Clear the job root, email, and the job body.")
                with Vertical(id="settings_grid"):
                    with Horizontal(classes="srow"):
                        yield Label("Jobs root directory")
                        yield Input(self.settings.get("job_root", ""), id="job_root", tooltip="Root directory where generated job subdirectories are written (or, in single-job mode, the directory the one job is written into directly).")
                        yield Button("Create", id="create_job_root", tooltip="Create the jobs root directory (shown only when the directory does not exist yet).")
                    with Horizontal(classes="srow"):
                        yield Label("Job name")
                        yield Input(self.settings.get("job_name", ""), id="job_name", tooltip="Optional job name: replaces the root-name prefix in the scheduler --job-name / -N. Empty keeps the default (root basename; single-job mode falls back to the root basename).")
                        yield Checkbox("single job in-place", id="single_job", value=self.settings.get("single_job", False), tooltip="Write one job directly in the jobs root (no numeric subdirectory, no sweep expansion); {{JOBID}} expands to the job name.")
                    with Horizontal(classes="srow"):
                        yield Label("Email (job notifications)")
                        yield Input(self.settings.get("email", ""), id="email", tooltip="Full address used in the scheduler --mail-user / -M field.")
                    if use_slurm:
                        with Horizontal(classes="srow"):
                            yield Label("Email notifications (SLURM)")
                            yield Input(", ".join(self.settings.get("mail_type") or ["ALL"]), id="mail_type_input", tooltip="Comma-separated SLURM --mail-type events (e.g. ALL, or BEGIN,FAIL). Options: ALL, NONE, BEGIN, END, FAIL, REQUEUE, TIME_LIMIT.")
                yield Label("Scheduler header (read-only)", classes="job_pane_label")
                yield TextArea("", id="job_header_view", read_only=True, tooltip="Scheduler header generated from settings; read-only.")
                with Horizontal(classes="job_pane_label_row"):
                    yield Label("Job body (after header)")
                    yield Checkbox("auto-wrap", id="job_body_wrap", value=True, tooltip="Toggle automatic line wrapping of the job body.")
                yield TokenTextArea("", id="job_body_editor", tooltip="Content placed right after the scheduler header. Tokens {{JOBID}} and {{JOBDIR}} are replaced per job; inline [[a,b]] lists generate one job per value. Fill it via Import job from file/text, or type directly.")
            with Vertical(id="pane_files"):
                pass
            with Vertical(id="pane_preview"):
                yield Horizontal(id="preview_split")
                with Horizontal(id="preview_conflict_bar"):
                    yield Static("", id="preview_conflicts", markup=False)
                    yield Button("Override conflicts", id="override_conflicts")
                with Horizontal(id="preview_actions"):
                    yield Label("Max parallel jobs", id="parallel_label")
                    yield Input(self.settings.get("parallelism_limit", "0"), id="parallel", placeholder="parallelism limit", tooltip="Max jobs run in parallel: 0 = no limit (all parallel), 1 = all in series, N = N parallel chains.")
                    yield Static("", id="preview_spacer")
                    yield Button("Write & Submit", id="submit", tooltip="Write job directories and submit them to the scheduler.")
                    yield Button("Log", id="log", tooltip="Show the log of the last submission.")
            with Vertical(id="pane_running"):
                with Horizontal(id="running_header"):
                    yield Label("Running jobs", id="running_title")
                    yield Checkbox("my jobs only", id="running_only_user", value=True, tooltip="Restrict the list to the current user's jobs; uncheck to show all jobs.")
                    yield Button("Refresh", id="refresh_running", tooltip="Query the scheduler for active jobs.")
                with Horizontal(id="running_controls"):
                    yield Input(placeholder="filter name/id", id="running_filter", tooltip="Filter jobs by name or id (substring).")
                    yield Select([("id", "id"), ("name", "name"), ("state", "state"), ("time", "time")],
                                 id="running_sort", value="time", tooltip="Sort order for the job list.")
                    yield Static("", id="running_spacer")
                    yield Button("Details", id="job_details", tooltip="Show full scheduler details for the selected job.")
                    yield Button("Cancel", id="cancel_job", tooltip="Cancel the selected job (scancel/qdel).")
                yield Label("", id="running_msg")
                yield DataTable(id="running_list", zebra_stripes=True)
        yield Label("", id="status_bar")

    async def on_mount(self):
        self._active = "settings"
        await self._rebuild_rows()
        self._activate("settings")
        info = {"slurm": "SLURM", "pbs": "PBS"}.get(scheduler, "No scheduler found")
        scheduler_info = self.query_one("#scheduler_info", Static)
        scheduler_info.update(info)
        scheduler_info.tooltip = {"slurm": "SLURM available (sbatch)",
                                  "pbs": "PBS available (qsub)"}.get(
                                      scheduler, "No scheduler detected")
        self.query_one("#parallel_label", Label).tooltip = (
            "Max jobs run in parallel: 0 = no limit (all parallel), "
            "1 = all in series, N = N parallel chains.")
        self._refresh_job_header()
        self._load_job_body()
        self._update_token_warning()
        if self.preset_name:
            self.apply_preset(dict(read_config()["presets"][self.preset_name]))
            self._set_status(f"Loaded preset '{self.preset_name}'")

    # ---- dynamic file tabs (openable/closable tab strip) ----
    # self._rows is the single source of truth for the open file tabs.
    # Rules for mutating the tab set (add/remove):
    #   * Rebuild the whole strip from _rows (_rebuild_rows) instead of
    #     diffing widgets, so tabs and panes never drift from the data.
    #   * The "+" button is mounted last, outside the _rows loop, so it is
    #     never counted as a file N and has no backing pane.
    #   * Rebuild produces 1:1 ids tab_file_{i} <-> FilePane, so indices in
    #     _rows, tab buttons, and panes always agree.
    #   * Keep _active valid: after any mutation it must be a real index (or
    #     fall back), never past the end.
    #   * Remove must not derive _rows from a filtered subset (e.g. non-empty
    #     paths) or it will silently drop empty-path tabs alongside the one
    #     being removed; rebuild from every remaining pane instead.
    #   * Drop per-tab state (file_edits entry) when a tab is removed, and
    #     persist _rows via included_files in collect_settings.

    async def _rebuild_rows(self):
        tabs = self.query_one("#file_tabs", Horizontal)
        panes = self.query_one("#pane_files", Vertical)
        await tabs.remove_children()
        await panes.remove_children()
        for i, row in enumerate(self._rows):
            path = row.get("path") if isinstance(row, dict) else row
            is_dir = bool(path) and os.path.isdir(path)
            mode = row.get("mode", "symlink" if is_dir else "copy") if isinstance(row, dict) else ("symlink" if is_dir else "copy")
            name = Path(path).name if path else f"file {i + 1}"
            if is_dir:
                tip = "Included directory (copied/symlinked into each job dir; not editable)."
            else:
                tip = "Edit included file (only the copy in the job dir changes; source untouched)."
            tabs.mount(Button(name, id=f"tab_file_{i}", tooltip=tip))
            panes.mount(FilePane(path, self.file_edits, mode))
        tabs.mount(Button("+", id="add_file", tooltip="Add an included file (copied into each generated job directory)."))
        self._activate(self._active if self._active in (i for i in range(len(self._rows))) else "settings")
        self._update_token_warning()

    def _activate(self, key):
        self._active = key
        settings = self.query_one("#pane_settings")
        preview = self.query_one("#pane_preview")
        files = self.query_one("#pane_files")
        running = self.query_one("#pane_running")
        settings.display = key == "settings"
        preview.display = key == "preview"
        running.display = key == "running"
        files.display = isinstance(key, int)
        for i, pane in enumerate(files.query(FilePane)):
            pane.display = key == i
        bar = self.query_one("#tab_bar")
        for b in list(bar.query(Button)) + list(self.query_one("#actions").query(Button)):
            b.remove_class("active")
        if key == "settings":
            bar.query_one("#tab_settings").add_class("active")
        elif key == "preview":
            self.query_one("#tab_preview").add_class("active")
        elif key == "running":
            self.query_one("#tab_jobs").add_class("active")
        elif isinstance(key, int):
            bar.query_one(f"#tab_file_{key}").add_class("active")

    # ---- files rows handlers ----

    async def on_button_pressed(self, event):
        bid = event.button.id
        if bid == "submit":
            self.action_submit()
        elif bid == "override_conflicts":
            self._override_conflicts = True
            self.action_submit()
        elif bid == "add_file":
            await self.action_add_file()
        elif bid == "help":
            self.push_screen(HelpScreen())
        elif bid == "presets":
            self.collect_settings()
            self.push_screen(PresetsScreen(self, self.settings))
        elif bid == "log":
            if getattr(self, "_submit_log", None):
                self.push_screen(SubmitLogScreen(self._submit_log))
        elif bid == "tab_settings":
            self._activate("settings")
        elif bid == "tab_preview":
            self.action_preview()
            self._activate("preview")
        elif bid == "tab_jobs":
            self._activate("running")
            await self._refresh_running()
        elif bid == "refresh_running":
            await self._refresh_running()
        elif bid == "job_details":
            rec = self._selected_job()
            if rec is None:
                self._set_status("Select a job to view details.")
                return
            text = await self._capture(detail_cmd(use_slurm, rec["job_id"]))
            self.push_screen(JobDetailsScreen(rec["job_id"], text))
        elif bid == "cancel_job":
            rec = self._selected_job()
            if rec is None:
                self._set_status("Select a job to cancel.")
                return
            self.push_screen(
                ConfirmScreen(f"Cancel job {rec['job_id']}?",
                              f"Cancel job {rec['job_id']}?"),
                callback=lambda ok: self._do_cancel(rec, ok))
        elif bid.startswith("tab_file_"):
            self._activate(int(bid.removeprefix("tab_file_")))
        elif bid == "remove_file":
            await self._remove(next((a for a in event.button.ancestors if isinstance(a, FilePane)), None))
        elif bid == "browse_file":
            pane = next((a for a in event.button.ancestors if isinstance(a, FilePane)), None)
            if pane is not None:
                start = pane.get_path() or self.settings.get("job_root", "")
                self.push_screen(FileBrowserScreen(start),
                                 callback=lambda p: self._browse_result(pane, p))
        elif bid == "clear_diff":
            pane = next((a for a in event.button.ancestors if isinstance(a, FilePane)), None)
            if pane is not None:
                pane.path = pane.get_path()
                self.file_edits.pop(pane.path, None)
                pane._reload_content()
        elif bid == "import_job":
            ext = ".slurm" if use_slurm else ".pbs"
            self.push_screen(FileBrowserScreen(os.getcwd(), filter_ext=ext),
                             callback=self._import_job_result)
        elif bid == "import_text":
            self.push_screen(TextInputScreen("Paste the job as text", "The pasted job is parsed into the email and job body; it is never written back to any file.",
                                             hint="Hint: use Shift+Ctrl+V to paste — plain Ctrl+V may not work in the terminal."),
                             callback=self._import_job_text)
        elif bid == "clear_job":
            self._clear_job_fields()
        elif bid == "create_job_root":
            self._create_job_root()

    def _browse_result(self, pane, path):
        if path:
            inp = pane.query_one("#file_path", Input)
            inp.value = path
            pane._reload_content()
            self._update_token_warning()

    def _import_job_result(self, path):
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            self._set_status("Could not read: " + path)
            return
        email, mail_types, body = parse_import(content, use_slurm)
        self._apply_import(email, mail_types, body, "file " + path)

    def _import_job_text(self, text):
        if not text:
            return
        email, mail_types, body = parse_import(text, use_slurm)
        self._apply_import(email, mail_types, body, "pasted text")

    def _apply_import(self, email, mail_types, body, source):
        self.settings["email"] = email
        self.query_one("#email", Input).value = email
        self.settings["mail_type"] = mail_types or ["ALL"]
        if use_slurm:
            self._set_mail_type(self.settings["mail_type"])
        self.settings["job_body"] = body
        self.query_one("#job_body_editor", TextArea).load_text(body)
        self._refresh_job_header()
        self._update_token_warning()
        self._schedule_autosave()
        self._set_status("Imported job from " + source)

    def _set_mail_type(self, mail_types):
        self.query_one("#mail_type_input", Input).value = ", ".join(mail_types or [])

    def _clear_job_fields(self):
        self.settings["job_root"] = os.getcwd()
        self.settings["email"] = ""
        self.settings["mail_type"] = ["ALL"]
        self._set_settings_inputs(self.settings)
        self.settings["job_body"] = "cd {{JOBDIR}}\n"
        self.query_one("#job_body_editor", TextArea).load_text("cd {{JOBDIR}}\n")
        self._refresh_job_header()
        self._update_token_warning()
        self._schedule_autosave()
        self._set_status("Cleared job fields")

    def _create_job_root(self):
        root = (self.settings.get("job_root") or "").strip()
        if not root:
            return
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            self._set_status(f"Could not create jobs root {root}: {e}")
            return
        self._update_token_warning()
        self._refresh_job_header()
        self._schedule_autosave()
        self._set_status(f"Created jobs root directory: {root}")

    async def _remove(self, pane):
        if pane is None:
            return
        path = pane.get_path()
        panes = self.query_one("#pane_files", Vertical)
        await panes.remove_children([pane])
        if path:
            self.file_edits.pop(path, None)
        self._rows = self._collect_paths()
        await self._rebuild_rows()

    def _collect_paths(self):
        rows = []
        for pane in self.query_one("#pane_files", Vertical).query(FilePane):
            rows.append({"path": pane.get_path(),
                         "mode": pane.get_mode()
                                 or ("symlink" if pane._is_dir() else "copy")})
        return rows

    async def action_add_file(self):
        self._rows.append("")
        await self._rebuild_rows()
        self._activate(len(self._rows) - 1)

    def on_input_changed(self, event):
        if event.input.id == "parallel":
            self.settings["parallelism_limit"] = event.input.value.strip()
            self._schedule_autosave()
            return
        if event.input.id == "running_filter":
            self._render_running()
            return
        if event.input.id == "file_path":
            pane = next((a for a in event.input.ancestors if isinstance(a, FilePane)), None)
            if pane is None:
                return
            panes = self.query_one("#pane_files", Vertical).query(FilePane)
            index = list(panes).index(pane)
            path = pane.get_path()
            name = Path(path).name if path else f"file {index + 1}"
            tab = self.query_one(f"#tab_file_{index}", Button)
            tab.label = name
            # The Button.label reactive repaints but does not re-measure, so the
            # tab keeps its old width in the auto-width bar; force a re-layout.
            tab.refresh(repaint=True, layout=True)
            return
        if any(getattr(a, "id", None) == "settings_grid" for a in event.input.ancestors):
            if event.input.id == "job_root":
                self.settings["job_root"] = event.input.value.strip()
            elif event.input.id == "job_name":
                self.settings["job_name"] = event.input.value.strip()
            elif event.input.id == "email":
                self.settings["email"] = event.input.value.strip()
            elif event.input.id == "mail_type_input":
                self.settings["mail_type"] = self._collect_mail_type()
            self._refresh_job_header()
            self._update_token_warning()
            self._schedule_autosave()

    # ---- preview / write / submit ----

    def _preview_split(self):
        split = self.query_one("#preview_split", Horizontal)
        if not split.query(Tree):
            tree = Tree("jobs", id="job_tree")
            tree.tooltip = "One node per generated job directory."
            split.mount(tree)
            split.mount(TextArea(read_only=True, id="job_viewer", tooltip="Content of the selected job file."))
        return split

    def _set_status(self, text):
        label = self.query_one("#status_bar", Label)
        label.remove_class("warn")
        label.update(text)
        self._status_is_warning = False

    def _validate_job_root(self):
        """Flag a missing/empty jobs root: red field + message for the status bar."""
        root = (self.settings.get("job_root") or "").strip()
        inp = self.query_one("#job_root", Input)
        self.query_one("#create_job_root", Button).display = bool(root) and not os.path.isdir(root)
        if not root:
            inp.add_class("invalid")
            return "jobs root is not set"
        if not os.path.isdir(root):
            inp.add_class("invalid")
            return f"jobs root does not exist: {root}"
        inp.remove_class("invalid")
        return ""

    def _update_token_warning(self):
        """Show unknown {{TOKEN}} placeholders and malformed data files in the
        global status bar. Clears when both are fixed."""
        warnings = []
        root_warning = self._validate_job_root()
        if root_warning:
            warnings.append(root_warning)
        job = self.query_one("#job_body_editor", TextArea).text
        unknown = unknown_tokens(job)
        if unknown:
            warnings.append("unknown token(s): "
                            + ", ".join("{{" + t + "}}" for t in unknown))
        warnings.extend(body_sweep_errors(job))
        pane_paths = []
        for p in self.query_one("#pane_files", Vertical).query(FilePane):
            if not p.is_mounted:
                continue
            try:
                path = p.get_path()
                if path:
                    pane_paths.append(path)
            except Exception:
                continue
        if len(pane_paths) != len(set(pane_paths)):
            warnings.append("duplicate included file path(s)")
        for pane in self.query_one("#pane_files", Vertical).query(FilePane):
            if not pane.is_mounted:
                continue
            try:
                path = pane.get_path()
                content = pane.get_content()
                prev = self._data_validation_cache.get(path)
                h = hash(content)
                if prev is None or prev[0] != h:
                    self._data_validation_cache[path] = (h, validate_data(path, content))
                err = self._data_validation_cache[path][1]
            except Exception:
                continue
            if err:
                name = Path(path).name or "<file>"
                warnings.append(f"{name}: {err}")
        label = self.query_one("#status_bar", Label)
        if warnings:
            label.update("Warning: " + "  |  ".join(warnings))
            label.add_class("warn")
            self._status_is_warning = True
        elif self._status_is_warning:
            label.update("")
            label.remove_class("warn")
            self._status_is_warning = False

    def _populate_tree(self, variants):
        split = self._preview_split()
        tree = split.query_one("#job_tree", Tree)
        tree.clear()
        tree.root.label = self.settings.get("job_root") or "jobs"
        viewer = split.query_one("#job_viewer", TextArea)
        viewer.load_text("")
        if not variants:
            return
        first_file_node = None
        for i, v in enumerate(variants, start=1):
            node = tree.root.add(
                f"{os.path.basename(v['job_path'])}  (Job {i}/{len(variants)})  [{v['label']}]",
                data=v,
            )
            job_file_node = node.add(os.path.basename(v["job_file"]), data={"path": v["job_file"], "content": v["job"]})
            for basename, content in v["files"].items():
                node.add(basename, data={"path": basename, "content": content})
            for d in v.get("dirs") or []:
                marker = "copy" if d.get("mode") == "copy" else "symlink"
                node.add(f"{d['basename']}/ ({marker})",
                         data={"path": d["basename"],
                               "content": f"[{marker}] directory: {d['path']}"})
            for fl in v.get("file_links") or []:
                node.add(f"{fl['basename']} (symlink)",
                         data={"path": fl["basename"],
                               "content": f"[symlink] file: {fl['path']}"})
            if first_file_node is None:
                first_file_node = job_file_node
        tree.root.expand_all()
        tree.call_after_refresh(lambda: tree.select_node(first_file_node))
        tree.call_after_refresh(lambda: tree.scroll_to_node(first_file_node))

    def on_tree_node_selected(self, event):
        data = event.node.data
        viewer = self.query_one("#job_viewer", TextArea)
        if not data or "content" not in data:
            viewer.load_text("")
            return
        content = data.get("content")
        if isinstance(content, bytes):
            viewer.load_text(f"(binary file, {len(content)} bytes)")
        else:
            viewer.load_text(str(content))

    def action_preview(self):
        self.collect_settings()
        variants, err, warnings = build_jobs(self.settings)
        if err:
            self._set_status("Error: " + err)
            return
        self._populate_tree(variants)
        msg = f"{len(variants)} job(s) generated. Use Write & Submit to write them to disk."
        conflicts = existing_targets(variants)
        bar = self.query_one("#preview_conflict_bar", Horizontal)
        submit = self.query_one("#submit", Button)
        if conflicts:
            self.query_one("#preview_conflicts", Static).update(
                "Would overwrite existing file(s):\n" + "\n".join(
                    f"  {c['kind']}: {c['path']}" for c in conflicts))
            bar.query_one("#preview_conflicts", Static).add_class("conflict")
            bar.query_one("#override_conflicts", Button).disabled = False
            bar.display = True
            submit.disabled = True
            msg = (f"{len(variants)} job(s) generated, but {len(conflicts)} target(s) "
                   "already exist — resolve them, or use Override conflicts to write anyway.")
        else:
            bar.query_one("#preview_conflicts", Static).update("")
            bar.query_one("#preview_conflicts", Static).remove_class("conflict")
            bar.display = False
            submit.disabled = False
            self._override_conflicts = False
        if warnings:
            msg += "  Warning: " + "; ".join(warnings)
        self._set_status(msg)

    def _write(self, override=None):
        variants, err, _warnings = build_jobs(self.settings)
        if err:
            self._set_status("Error: " + err)
            return
        if override is None:
            override = getattr(self, "_override_conflicts", False)
        conflicts = existing_targets(variants)
        if conflicts:
            if not override:
                self._set_status(
                    "Error: would overwrite existing file(s): "
                    + ", ".join(f"{c['kind']}: {c['path']}" for c in conflicts))
                return
            self._override_conflicts = False
        for v in variants:
            os.makedirs(v["job_path"], exist_ok=True)
            with open(v["job_file"], "w", encoding="utf-8") as f:
                f.write(v["job"])
            for basename, content in v["files"].items():
                target = os.path.join(v["job_path"], basename)
                if isinstance(content, bytes):
                    with open(target, "wb") as f:
                        f.write(content)
                else:
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(content)
            for d in v.get("dirs") or []:
                target = os.path.join(v["job_path"], d["basename"])
                try:
                    if d.get("mode") == "copy":
                        shutil.copytree(d["path"], target, dirs_exist_ok=True)
                    else:
                        os.symlink(d["path"], target)
                except OSError as exc:
                    self._set_status(
                        f"Error copying/symlinking {d['basename']}: {exc}")
                    return
            for fl in v.get("file_links") or []:
                target = os.path.join(v["job_path"], fl["basename"])
                try:
                    os.symlink(fl["path"], target)
                except OSError as exc:
                    self._set_status(
                        f"Error symlinking {fl['basename']}: {exc}")
                    return
        self._populate_tree(variants)
        self._set_status(f"Wrote {len(variants)} job(s) under {self.settings['job_root']}.")
        return variants

    def _confirm_submit(self, submit):
        if not submit:
            return
        variants = self._write()
        if not variants:
            return
        root = self.settings["job_root"]
        try:
            parallelism = int(self.settings.get("parallelism_limit") or 0)
        except ValueError:
            parallelism = 0
        if parallelism < 0:
            parallelism = 0
        self._submit_log, submitted, failed = run_submission(
            variants, root, parallelism, scheduler, use_slurm)
        self._write_submit_log()
        msg = f"{submitted} job(s) submitted"
        if failed:
            msg += f", {failed} failed"
        self._set_status(msg)
        if failed:
            self.push_screen(SubmitLogScreen(self._submit_log))

    def _write_submit_log(self):
        try:
            with open(os.path.join(self.settings["job_root"], "submit.log"), "w",
                      encoding="utf-8") as f:
                f.write(format_submit_log(getattr(self, "_submit_log", [])))
        except OSError:
            pass

    async def _refresh_running(self):
        msg = self.query_one("#running_msg", Label)
        lst = self.query_one("#running_list", DataTable)
        self._running_rows = []
        if scheduler is None:
            msg.update("No scheduler found.")
            msg.display = True
            lst.clear()
            return
        if use_slurm:
            cmd = ["squeue", "--format=%i|%u|%j|%T|%M|%l"]
        else:
            cmd = ["qstat"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _err = await proc.communicate()
        except OSError:
            msg.update("Could not query the scheduler.")
            msg.display = True
            lst.clear()
            return
        text = out.decode(errors="replace")
        self._running_rows = parse_running_output(text, use_slurm)
        self._render_running()

    def _render_running(self):
        msg = self.query_one("#running_msg", Label)
        table = self.query_one("#running_list", DataTable)
        if not table.columns:
            table.add_columns("Job ID", "Name", "State", "Time", "Queue")
        rows = list(getattr(self, "_running_rows", []) or [])
        only_user = self.query_one("#running_only_user", Checkbox).value
        user = self.settings.get("cluster_user") or getpass.getuser()
        if only_user:
            rows = [r for r in rows if r["user"] == user]
        filt = self.query_one("#running_filter", Input).value.strip().lower()
        if filt:
            rows = [r for r in rows
                    if filt in r["name"].lower() or filt in r["job_id"].lower()]
        sortkey = self.query_one("#running_sort", Select).value or "time"
        rows = sorted(rows, key=lambda r: self._running_sort_key(r, sortkey))
        self._running_visible = rows
        table.clear()
        if rows:
            for r in rows:
                table.add_row(self._running_cells(r))
        if rows:
            msg.display = False
        else:
            msg.update("No running jobs.")
            msg.display = True

    def _running_cells(self, r):
        colors = {"running": "green", "pending": "yellow", "completed": "cyan",
                  "cancelled": "magenta", "failed": "red", "unknown": "default"}
        cat = state_category(r["state"])
        state = Text(r["state"], style=colors.get(cat, "default"))
        if r["limit_sec"] and r["elapsed_sec"] is not None:
            pct, bar = progress_cells(r["elapsed_sec"], r["limit_sec"])
            time = Text(f"{self._fmt_dur(r['elapsed_sec'])}/{self._fmt_dur(r['limit_sec'])} ")
            time.append(bar, style="red" if pct == 100 else "green")
            time.append(f" {pct}%")
        else:
            time = Text(self._fmt_dur(r["elapsed_sec"]))
        return (r["job_id"], r["name"], state, time, r["queue"])

    @staticmethod
    def _running_sort_key(r, key):
        if key == "id":
            m = re.search(r"\d+", r["job_id"])
            return (0, int(m.group())) if m else (1, r["job_id"])
        if key == "name":
            return (0, r["name"].lower())
        if key == "state":
            order = {"running": 0, "pending": 1, "completed": 2,
                     "cancelled": 3, "failed": 4, "unknown": 5}
            return (order.get(state_category(r["state"]), 5), r["state"])
        return (r["elapsed_sec"] if r["elapsed_sec"] is not None else -1, r["job_id"])

    @staticmethod
    def _fmt_dur(sec):
        if sec is None:
            return "--"
        h, rem = divmod(int(sec), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    def _selected_job(self):
        table = self.query_one("#running_list", DataTable)
        rows = getattr(self, "_running_visible", []) or []
        idx = table.cursor_row
        if idx is None or not (0 <= idx < len(rows)):
            return None
        return rows[idx]

    async def _capture(self, cmd):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await proc.communicate()
            return (out.decode(errors="replace") + err.decode(errors="replace")).strip()
        except OSError as e:
            return str(e)

    async def _do_cancel(self, rec, ok):
        if not ok:
            return
        text = await self._capture(cancel_cmd(scheduler, rec["job_id"]))
        if text:
            self._set_status(f"Could not cancel {rec['job_id']}: {text}")
        else:
            self._set_status(f"Cancelled job {rec['job_id']}.")
        await self._refresh_running()

    def action_submit(self):
        self.collect_settings()
        self.push_screen(PromptScreen("Submit", "Write jobs to disk and submit them?", "yes"),
                         callback=self._confirm_submit)

    def action_save(self):
        self.collect_settings()
        self.cfg["last"] = self.settings
        self.cfg["selected_preset"] = self.cfg.get("selected_preset", "")
        write_config(self.cfg)
        self._set_status("Configuration saved.")

    def _schedule_autosave(self):
        if self._autosave_timer is not None:
            self._autosave_timer.stop()
        self._autosave_timer = self.set_timer(1.5, self._autosave)

    def _autosave(self):
        self._autosave_timer = None
        try:
            self.collect_settings()
        except Exception:
            return
        self.cfg["last"] = self.settings
        self.cfg["selected_preset"] = self.cfg.get("selected_preset", "")
        write_config(self.cfg)

    def on_text_area_changed(self, event):
        if event.text_area.id in ("job_body_editor", "file_content"):
            self._schedule_autosave()
            self._update_token_warning()

    def on_select_changed(self, event):
        if event.select.id == "running_sort":
            self._render_running()

    def on_checkbox_changed(self, event):
        if event.checkbox.id == "job_body_wrap":
            self.query_one("#job_body_editor", TextArea).soft_wrap = event.checkbox.value
        elif event.checkbox.id == "single_job":
            self.settings["single_job"] = event.checkbox.value
            self._refresh_job_header()
            self._schedule_autosave()
        elif event.checkbox.id == "running_only_user":
            self._render_running()

    def action_quit(self):
        self.exit()


def _resolve_preset(name):
    """Return the settings dict for a saved preset, or raise SystemExit if the
    name is unknown. Honours the configured config file (incl. -c)."""
    cfg = read_config()
    if name not in cfg["presets"]:
        raise SystemExit(f"forgebatch: preset not found: {name}")
    return dict(cfg["presets"][name])


def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="forgebatch", description="SLURM/PBS job directory generator TUI")
    parser.add_argument("-c", "--config", metavar="PATH",
                        help="configuration file "
                             "(default: ~/.config/forgebatch/config.json)")
    parser.add_argument("preset", nargs="?", metavar="PRESET",
                        help="load a saved preset by name on startup")
    args = parser.parse_args()
    if args.config:
        config.set_config_file(args.config)
    if args.preset:
        _resolve_preset(args.preset)
        ForgeBatchApp(preset=args.preset).run()
    else:
        ForgeBatchApp().run()


if __name__ == "__main__":
    main()
