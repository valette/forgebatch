"""Integration test: the preview shows placeholder-substituted job content.

This exercises the full TUI path (editor -> collect_settings -> build_jobs ->
preview tree -> job viewer) to guard against regressions where typed
{{JOBID}}/{{JOBDIR}} tokens fail to reach the generated job shown in the
Preview tab.

Run from the forgebatch/ repo root:
    python3 -m unittest discover -s tests
"""

import asyncio
import getpass
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from rich.text import Text  # noqa: E402
from textual.containers import Horizontal  # noqa: E402
from textual.coordinate import Coordinate  # noqa: E402
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static, TextArea, Tree  # noqa: E402

import config as config_mod  # noqa: E402
import runner as runner_mod  # noqa: E402
from app import ForgeBatchApp  # noqa: E402
from screens import PresetsScreen  # noqa: E402
from screens import JobDetailsScreen  # noqa: E402
from widgets import FilePane  # noqa: E402
from diffing import compute_patch  # noqa: E402


def _running_items_text(app):
    """Concatenate the rendered text of every row in the running-job DataTable."""
    table = app.query_one("#running_list", DataTable)
    out = []
    for i in range(table.row_count):
        cells = table.get_row_at(i)
        row = []
        for c in cells:
            row.append(c.plain if isinstance(c, Text) else str(c))
        out.append(" ".join(row))
    return "\n".join(out)


class PreviewSubstitutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config_mod.set_config_file(os.path.join(self.tmp, "config.json"))
        self.root = os.path.join(self.tmp, "jobs")

    async def test_preview_shows_resolved_tokens(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("echo {{JOBID}} {{JOBDIR}}\n")
            await pilot.pause()
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            viewer = app.query_one("#job_viewer", TextArea)
            txt = viewer.text
            resolved = "echo 1 " + os.path.join(self.root, "1")
            self.assertIn(resolved, txt)
            self.assertNotIn("{{JOBID}}", txt)
            self.assertNotIn("{{JOBDIR}}", txt)

    async def test_file_pane_dir_mode_hides_editor(self):
        src = os.path.join(self.tmp, "assets")
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("hi")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [{"path": src}]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query(FilePane).nodes[0]
            self.assertEqual(pane.get_kind(), "dir")
            self.assertFalse(pane.query_one("#file_content").display)
            self.assertTrue(pane.query_one("#symlink").display)
            self.assertTrue(pane.query_one("#symlink", Checkbox).value)
            self.assertEqual(pane.get_mode(), "symlink")

    async def test_directory_listing_is_top_level_only(self):
        src = os.path.join(self.tmp, "assets")
        os.makedirs(os.path.join(src, "nested"))
        with open(os.path.join(src, "a.txt"), "w") as f:
            f.write("a")
        with open(os.path.join(src, "nested", "b.txt"), "w") as f:
            f.write("b")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [{"path": src}]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query(FilePane).nodes[0]
            listing = pane._listing()
            self.assertIn("a.txt", listing)
            self.assertIn("nested/", listing)
            # top-level only: nested files are never walked
            self.assertNotIn("b.txt", listing)

    async def test_write_copies_included_directory(self):
        src = os.path.join(self.tmp, "assets")
        os.makedirs(os.path.join(src, "nested"))
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("hi")
        with open(os.path.join(src, "nested", "g.txt"), "w") as f:
            f.write("yo")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app._rows = [{"path": src, "mode": "copy"}]
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            self.assertEqual(app.settings["included_files"], [])
            self.assertEqual(app.settings["included_dirs"],
                             [{"path": src, "mode": "copy"}])
            variants = app._write()
            await pilot.pause()
            self.assertEqual(len(variants), 1)
            job_path = variants[0]["job_path"]
            self.assertTrue(os.path.isdir(os.path.join(job_path, "assets")))
            self.assertTrue(os.path.isfile(os.path.join(job_path, "assets", "f.txt")))
            self.assertTrue(os.path.isfile(os.path.join(job_path, "assets", "nested", "g.txt")))
            self.assertFalse(os.path.islink(os.path.join(job_path, "assets")))

    async def test_write_symlinks_included_directory_by_default(self):
        src = os.path.join(self.tmp, "assets")
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("hi")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app._rows = [{"path": src}]
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            self.assertEqual(app.settings["included_dirs"],
                             [{"path": src, "mode": "symlink"}])
            variants = app._write()
            await pilot.pause()
            self.assertEqual(len(variants), 1)
            link = os.path.join(variants[0]["job_path"], "assets")
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.path.realpath(link), src)


    async def test_write_symlinks_included_file(self):
        src = os.path.join(self.tmp, "data.txt")
        with open(src, "w") as f:
            f.write("hi\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app._rows = [{"path": src, "mode": "symlink"}]
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            self.assertEqual(app.settings["included_files"],
                             [{"path": src, "mode": "symlink"}])
            self.assertEqual(app.file_edits, {})
            variants = app._write()
            await pilot.pause()
            self.assertEqual(len(variants), 1)
            link = os.path.join(variants[0]["job_path"], "data.txt")
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.path.realpath(link), src)

    async def test_file_pane_file_symlink_mode_hides_editor(self):
        src = os.path.join(self.tmp, "data.txt")
        with open(src, "w") as f:
            f.write("hi\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [{"path": src, "mode": "symlink"}]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query(FilePane).nodes[0]
            self.assertEqual(pane.get_kind(), "file")
            self.assertEqual(pane.get_mode(), "symlink")
            self.assertFalse(pane.query_one("#file_content").display)
            self.assertTrue(pane.query_one("#readonly").display)
            self.assertFalse(pane.query_one("#show_diff").display)

    async def test_collect_rows_symlink_file_has_no_edit(self):
        src = os.path.join(self.tmp, "data.txt")
        with open(src, "w") as f:
            f.write("hi\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [{"path": src, "mode": "symlink"}]
            await app._rebuild_rows()
            await pilot.pause()
            files, dirs = app._collect_rows()
            self.assertEqual(files, [{"path": src, "mode": "symlink"}])
            self.assertEqual(dirs, [])
            self.assertEqual(app.file_edits, {})

    async def test_show_diff_renders_side_by_side(self):
        src = os.path.join(self.tmp, "data.txt")
        with open(src, "w") as f:
            f.write("line1\nline2\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [{"path": src}]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query(FilePane).nodes[0]
            editor = pane.query_one("#file_content", TextArea)
            editor.load_text("line1\nline2 changed\n")
            await pilot.pause()
            self.assertFalse(pane.query_one("#diff_view").display)
            show = pane.query_one("#show_diff", Checkbox)
            show.value = True
            pane.on_checkbox_changed(type("E", (), {"checkbox": show})())
            await pilot.pause()
            self.assertTrue(pane.query_one("#diff_view").display)
            self.assertTrue(pane.query_one("#file_content").display)
            expected = compute_patch("line1\nline2\n", "line1\nline2 changed\n")
            self.assertEqual(pane.query_one("#diff_view", Static).content, expected)

    async def test_clear_diff_discards_edits_and_restores_source(self):
        src = os.path.join(self.tmp, "data.txt")
        with open(src, "w") as f:
            f.write("orig\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [src]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query_one(FilePane)
            editor = pane.query_one("#file_content", TextArea)
            editor.load_text("edited\n")
            await pilot.pause()
            app._collect_rows()
            self.assertIn(src, app.file_edits)
            from textual.widgets import Button
            clear = pane.query_one("#clear_diff", Button)
            await app.on_button_pressed(type("E", (), {"button": clear})())
            await pilot.pause()
            self.assertNotIn(src, app.file_edits)
            self.assertEqual(pane.get_content(), "orig\n")

    async def test_remove_preserves_remaining_modes(self):
        a = os.path.join(self.tmp, "a.txt")
        b = os.path.join(self.tmp, "b.txt")
        with open(a, "w") as f:
            f.write("a\n")
        with open(b, "w") as f:
            f.write("b\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [{"path": a, "mode": "symlink"}, {"path": b}]
            await app._rebuild_rows()
            await pilot.pause()
            panes = app.query_one("#pane_files").query(FilePane).nodes
            self.assertEqual([p.get_mode() for p in panes], ["symlink", "copy"])
            # Remove the second (copy-mode) file; the first must keep "symlink".
            await app._remove(panes[1])
            await pilot.pause()
            remaining = app.query_one("#pane_files").query(FilePane).nodes
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].get_mode(), "symlink")
            app.collect_settings()
            self.assertEqual(app.settings["included_files"],
                             [{"path": a, "mode": "symlink"}])



    async def test_preview_body_sweep_expands_jobs(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("python train.py --lr [[1,2,3]]\n")
            await pilot.pause()
            app._rows = []
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            viewer = app.query_one("#job_viewer", TextArea)
            self.assertIn("python train.py --lr 1", viewer.text)
            self.assertNotIn("[[", viewer.text)
            self.assertNotIn("--lr [[", viewer.text)

    async def test_single_job_mode_updates_header_and_preview(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            header = app.query_one("#job_header_view", TextArea)
            self.assertIn("{{JOBID}}", header.text)
            app.query_one("#single_job", Checkbox).value = True
            await pilot.pause()
            self.assertNotIn("{{JOBID}}", header.text)
            self.assertNotIn("-{{JOBID}}", header.text)
            app.collect_settings()
            self.assertTrue(app.settings["single_job"])
            app.action_preview()
            await pilot.pause()
            tree = app.query_one("#job_tree", Tree)
            self.assertEqual(len(tree.root.children), 1)

    async def test_preview_conflicts_listed_and_submit_blocked(self):
        os.makedirs(self.root)
        with open(os.path.join(self.root, "job.slurm"), "w") as f:
            f.write("existing\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app.query_one("#single_job", Checkbox).value = True
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            bar = app.query_one("#preview_conflicts", Static)
            self.assertIn("Would overwrite existing file", str(bar.render()))
            self.assertIn("job file", str(bar.render()))
            self.assertTrue(app.query_one("#submit", Button).disabled)
            self.assertIsNone(app._write())
            self.assertIn("would overwrite", str(app.query_one("#status_bar").render()))

    async def test_preview_conflicts_clear_and_submit_reenabled(self):
        os.makedirs(self.root)
        with open(os.path.join(self.root, "job.slurm"), "w") as f:
            f.write("existing\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app.query_one("#single_job", Checkbox).value = True
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            self.assertTrue(app.query_one("#submit", Button).disabled)
            os.remove(os.path.join(self.root, "job.slurm"))
            app.action_preview()
            await pilot.pause()
            bar = app.query_one("#preview_conflicts", Static)
            self.assertEqual(str(bar.render()).strip(), "")
            self.assertFalse(app.query_one("#submit", Button).disabled)

    async def test_preview_conflicts_override_submits_anyway(self):
        import app as app_mod
        os.makedirs(self.root)
        job_file = os.path.join(self.root, "job.slurm")
        with open(job_file, "w") as f:
            f.write("existing\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app.query_one("#single_job", Checkbox).value = True
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            self.assertTrue(app.query_one("#submit", Button).disabled)
            self.assertTrue(
                app.query_one("#preview_conflict_bar", Horizontal).display)
            self.assertFalse(
                app.query_one("#override_conflicts", Button).disabled)
            old_slurm = app_mod.use_slurm
            old_scheduler = app_mod.scheduler
            old_run = runner_mod.subprocess.run
            app_mod.use_slurm = True
            app_mod.scheduler = "slurm"
            try:
                runner_mod.subprocess.run = lambda cmd, **kw: types.SimpleNamespace(
                    returncode=0, stdout="101\n", stderr="")
                app._override_conflicts = True
                app._confirm_submit(True)
            finally:
                app_mod.use_slurm = old_slurm
                app_mod.scheduler = old_scheduler
                runner_mod.subprocess.run = old_run
            await pilot.pause()
            with open(job_file) as f:
                content = f.read()
            self.assertNotEqual(content, "existing\n")
            self.assertFalse(app._override_conflicts)
            self.assertIn("1 job(s) submitted",
                          str(app.query_one("#status_bar", Label).render()))

    async def test_job_header_preview_uses_double_brace_token(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            header = app.query_one("#job_header_view", TextArea)
            self.assertIn("{{JOBID}}", header.text)
            self.assertNotIn("<JOBID>", header.text)

    async def test_preview_warns_on_unknown_token(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("cd {{JODDIR}}\n")
            await pilot.pause()
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            status = app.query_one("#status_bar", Label).render()
            self.assertIn("{{JODDIR}}", str(status))
            self.assertNotIn("Error", str(status))

    async def test_status_bar_live_warns_on_unknown_token_and_clears(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#job_root", Input).value = self.tmp
            await pilot.pause()
            bar = app.query_one("#status_bar", Label)
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("cd {{JODDIR}}\n")
            await pilot.pause()
            self.assertIn("{{JODDIR}}", str(bar.render()))
            self.assertTrue(bar.has_class("warn"))
            editor.load_text("cd {{JOBDIR}}\n")
            await pilot.pause()
            self.assertEqual(bar.render(), "")
            self.assertFalse(bar.has_class("warn"))

    async def test_status_bar_warns_on_mixed_case_typo(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            bar = app.query_one("#status_bar", Label)
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("cd {{JOBDIssR}}\n")
            await pilot.pause()
            self.assertIn("{{JOBDIssR}}", str(bar.render()))
            self.assertTrue(bar.has_class("warn"))

    async def test_token_textarea_renders_token_line(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("cd {{JOBDISSR}}\necho {{JOBDIR}}\n")
            await pilot.pause()
            for y in range(len(editor.document.lines)):
                editor._render_line(y)

    async def test_status_bar_warns_on_malformed_data_file_and_clears(self):
        data = os.path.join(self.tmp, "specs.json")
        with open(data, "w") as f:
            f.write('{"x": 1}\n')
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#job_root", Input).value = self.tmp
            await pilot.pause()
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            bar = app.query_one("#status_bar", Label)
            self.assertEqual(bar.render(), "")
            area = app.query_one("#file_content", TextArea)
            area.load_text("{oops")
            await pilot.pause()
            self.assertTrue(bar.has_class("warn"))
            self.assertIn("malformed JSON", str(bar.render()))
            area.load_text('{"x": 1}\n')
            await pilot.pause()
            self.assertEqual(bar.render(), "")
            self.assertFalse(bar.has_class("warn"))

    async def test_status_bar_warns_on_malformed_body_sweep_and_clears(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#job_root", Input).value = self.tmp
            await pilot.pause()
            bar = app.query_one("#status_bar", Label)
            self.assertEqual(bar.render(), "")
            editor = app.query_one("#job_body_editor", TextArea)
            editor.load_text("echo [[1,2,\n")
            await pilot.pause()
            self.assertTrue(bar.has_class("warn"))
            self.assertIn("malformed script sweep", str(bar.render()))
            editor.load_text("echo [[1,2,3]]\n")
            await pilot.pause()
            self.assertEqual(bar.render(), "")
            self.assertFalse(bar.has_class("warn"))

    async def test_empty_file_tabs_do_not_warn_duplicate_path(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#job_root", Input).value = self.tmp
            await pilot.pause()
            app._rows = ["", ""]
            await app._rebuild_rows()
            await pilot.pause()
            bar = app.query_one("#status_bar", Label)
            self.assertNotIn("duplicate", str(bar.render()))

    async def test_job_body_editor_min_height(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 14)) as pilot:
            await pilot.pause()
            editor = app.query_one("#job_body_editor", TextArea)
            self.assertGreaterEqual(editor.region.height, 6)

    async def test_slurm_mail_type_checkboxes_and_import_roundtrip(self):
        from textual.widgets import Static, Checkbox
        import app as app_mod
        app = ForgeBatchApp()
        old_slurm = app_mod.use_slurm
        app_mod.use_slurm = True
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertTrue(app.query_one("#mail_type_ALL", Checkbox).value)
                app.query_one("#mail_type_ALL", Checkbox).value = False
                app.query_one("#mail_type_BEGIN", Checkbox).value = True
                app.query_one("#mail_type_FAIL", Checkbox).value = True
                await pilot.pause()
                app.collect_settings()
                self.assertEqual(app.settings["mail_type"], ["BEGIN", "FAIL"])
                text = ("#!/bin/sh\n#SBATCH --mail-type=END,FAIL\n"
                        "#SBATCH --mail-user=alice@example.com\nhostname\n")
                email, mail_types, body = app_mod.parse_import(text, True)
                self.assertEqual(mail_types, ["END", "FAIL"])
                app._apply_import(email, mail_types, body, "test")
                await pilot.pause()
                self.assertTrue(app.query_one("#mail_type_END", Checkbox).value)
                self.assertTrue(app.query_one("#mail_type_FAIL", Checkbox).value)
                self.assertFalse(app.query_one("#mail_type_ALL", Checkbox).value)
        finally:
            app_mod.use_slurm = old_slurm

    async def test_mail_type_checkbox_updates_scheduler_header(self):
        from textual.widgets import Static, Checkbox
        import app as app_mod
        app = ForgeBatchApp()
        old_slurm = app_mod.use_slurm
        app_mod.use_slurm = True
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.settings["email"] = "alice@example.com"
                app._refresh_job_header()
                await pilot.pause()
                header = app.query_one("#job_header_view", TextArea).text
                self.assertIn("#SBATCH --mail-type=ALL", header)
                app.query_one("#mail_type_ALL", Checkbox).value = False
                app.query_one("#mail_type_BEGIN", Checkbox).value = True
                app.query_one("#mail_type_FAIL", Checkbox).value = True
                await pilot.pause()
                header = app.query_one("#job_header_view", TextArea).text
                self.assertIn("#SBATCH --mail-type=BEGIN,FAIL", header)
                self.assertNotIn("--mail-type=ALL", header)
        finally:
            app_mod.use_slurm = old_slurm

    async def test_screenshot_saves_to_cwd(self):
        import time
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        old = os.getcwd()
        os.chdir(tmp)
        app = ForgeBatchApp()
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.deliver_screenshot(filename="shot.svg")
                target = os.path.join(tmp, "shot.svg")
                for _ in range(200):
                    if os.path.exists(target):
                        break
                    await pilot.pause()
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(target), "screenshot not written to cwd")
        finally:
            os.chdir(old)

    async def test_job_body_auto_wrap_toggles_soft_wrap(self):
        from textual.widgets import Checkbox
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            box = app.query_one("#job_body_wrap", Checkbox)
            editor = app.query_one("#job_body_editor", TextArea)
            self.assertTrue(editor.soft_wrap)
            box.value = False
            await pilot.pause()
            self.assertFalse(editor.soft_wrap)
            box.value = True
            await pilot.pause()
            self.assertTrue(editor.soft_wrap)

    async def test_file_pane_auto_wrap_toggles_soft_wrap(self):
        from textual.widgets import Checkbox
        from widgets import FilePane
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("line\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query_one(FilePane)
            box = pane.query_one("#file_wrap", Checkbox)
            area = pane.query_one("#file_content", TextArea)
            self.assertTrue(area.soft_wrap)
            box.value = False
            await pilot.pause()
            self.assertFalse(area.soft_wrap)
            box.value = True
            await pilot.pause()
            self.assertTrue(area.soft_wrap)

    async def test_file_tab_width_tracks_label_length(self):
        from textual.widgets import Button
        from widgets import FilePane
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.action_add_file()
            await pilot.pause()
            pane = app.query_one("#pane_files").query_one(FilePane)
            inp = pane.query_one("#file_path", Input)
            tab = app.query_one("#tab_file_0", Button)
            base = tab.size.width
            long = os.path.join(self.tmp, "a_very_long_filename_to_test_ellipsis.txt")
            with open(long, "w") as f:
                f.write("x\n")
            inp.value = long
            for _ in range(8):
                await pilot.pause()
            self.assertGreater(tab.size.width, base,
                               "tab width should grow to fit the new name")
            short = os.path.join(self.tmp, "x.txt")
            with open(short, "w") as f:
                f.write("x\n")
            inp.value = short
            for _ in range(8):
                await pilot.pause()
            self.assertLess(tab.size.width, base + 10,
                            "tab width should shrink for a short name")

    async def test_edited_binary_file_is_marked_binary(self):
        import diffing
        from widgets import FilePane
        data = os.path.join(self.tmp, "blob.bin")
        with open(data, "wb") as f:
            f.write(b"\x00\x01\x02")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Simulate an in-session edit on a binary base file.
            app.file_edits[data] = diffing.compute_patch("", "x\n")
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query_one(FilePane)
            self.assertTrue(pane.is_binary)
            self.assertTrue(pane.get_content().startswith("(binary file"))

    async def test_binary_file_is_not_editable(self):
        data = os.path.join(self.tmp, "blob.bin")
        with open(data, "wb") as f:
            f.write(b"\x00\x01\x02")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            pane = app.query_one("#pane_files").query_one(FilePane)
            area = pane.query_one("#file_content", TextArea)
            self.assertTrue(area.read_only)
            self.assertFalse(pane.query_one("#file_content").display)
            self.assertFalse(pane.query_one("#show_diff").display)
            self.assertTrue(pane.query_one("#symlink").display)
            readonly = pane.query_one("#readonly", Static)
            self.assertTrue(readonly.display)
            text = str(readonly.render())
            self.assertIn("binary file", text)
            self.assertIn("editing disabled", text)

    async def test_job_root_missing_marks_input_invalid_and_warns(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            missing = os.path.join(self.tmp, "nope")
            app.query_one("#job_root", Input).value = missing
            await pilot.pause()
            root_inp = app.query_one("#job_root", Input)
            self.assertTrue(root_inp.has_class("invalid"))
            bar = app.query_one("#status_bar", Label)
            self.assertIn("jobs root does not exist", str(bar.render()))
            app.query_one("#job_root", Input).value = self.tmp
            await pilot.pause()
            self.assertFalse(root_inp.has_class("invalid"))
            self.assertEqual(bar.render(), "")

    async def test_empty_job_root_marks_input_invalid(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#job_root", Input).value = ""
            await pilot.pause()
            self.assertTrue(app.query_one("#job_root", Input).has_class("invalid"))
            self.assertIn("jobs root is not set",
                          str(app.query_one("#status_bar", Label).render()))

    async def test_help_button_opens_help_screen(self):
        from screens import HelpScreen
        from textual.widgets import Markdown
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(HelpScreen())
            await pilot.pause()
            md = app.screen.query_one(Markdown)
            text = md.source
            self.assertIn("forgebatch", text)
            self.assertIn("{{JOBID}}", text)
            self.assertIn("{{JOBDIR}}", text)
            self.assertIn("Parallelism limit", text)
            app.screen.dismiss(None)
            await pilot.pause()
            self.assertNotIsInstance(app.screen, HelpScreen)

    async def test_submit_creates_dependency_chains(self):
        import types
        import app as app_mod
        specs = os.path.join(self.tmp, "specs.json")
        with open(specs, "w") as f:
            f.write('{"CodeLength": 256}\n')
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            app._rows = [specs]
            await app._rebuild_rows()
            await pilot.pause()
            editor = app.query_one("#file_content", TextArea)
            editor.load_text('{"CodeLength": [256, 128, 23]}\n')
            await pilot.pause()
            app.query_one("#parallel", Input).value = "2"
            app.collect_settings()

            calls = []
            returned = []
            counter = {"id": 100}
            old_slurm = app_mod.use_slurm
            old_scheduler = app_mod.scheduler
            old_run = runner_mod.subprocess.run
            app_mod.use_slurm = True
            app_mod.scheduler = "slurm"
            try:
                def fake_run(cmd, **kwargs):
                    calls.append(list(cmd))
                    counter["id"] += 1
                    returned.append(str(counter["id"]))
                    return types.SimpleNamespace(
                        returncode=0, stdout=str(counter["id"]) + "\n", stderr="")
                runner_mod.subprocess.run = fake_run
                app._confirm_submit(True)
            finally:
                app_mod.use_slurm = old_slurm
                app_mod.scheduler = old_scheduler
                runner_mod.subprocess.run = old_run

            self.assertEqual(len(calls), 3)
            bases = [os.path.basename(c[-1]) for c in calls]
            self.assertEqual(bases[0], "job.slurm")
            deps = [c for c in calls if any(a.startswith("--dependency=") for a in c)]
            self.assertEqual(len(deps), 1)
            self.assertEqual([a for a in deps[0] if a.startswith("--dependency=")],
                             ["--dependency=afterok:" + returned[0]])
            self.assertEqual(returned[0], "101")
            status = str(app.query_one("#status_bar", Label).render())
            self.assertIn("3 job(s) submitted", status)

    async def _run_submit(self, app, run, checks):
        import app as app_mod
        specs = os.path.join(self.tmp, "specs.json")
        with open(specs, "w") as f:
            f.write('{"CodeLength": 256}\n')
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            app._rows = [specs]
            await app._rebuild_rows()
            await pilot.pause()
            editor = app.query_one("#file_content", TextArea)
            editor.load_text('{"CodeLength": [256, 128]}\n')
            await pilot.pause()
            app.query_one("#parallel", Input).value = "1"
            app.collect_settings()
            old_slurm = app_mod.use_slurm
            old_scheduler = app_mod.scheduler
            old_run = runner_mod.subprocess.run
            app_mod.use_slurm = True
            app_mod.scheduler = "slurm"
            try:
                runner_mod.subprocess.run = run
                app._confirm_submit(True)
            finally:
                app_mod.use_slurm = old_slurm
                app_mod.scheduler = old_scheduler
                runner_mod.subprocess.run = old_run
            await pilot.pause()
            checks(app, pilot)
            await pilot.pause()

    async def test_submit_failure_opens_log_modal_and_writes_file(self):
        from screens import SubmitLogScreen
        import types
        calls = []

        def failing_run(cmd, **kwargs):
            calls.append(list(cmd))
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="error: invalid node name\n")

        def check(app, pilot):
            self.assertEqual(len(calls), 2)
            for cmd in calls:
                self.assertTrue(os.path.isfile(cmd[-1]),
                                f"job file does not exist: {cmd[-1]}")
            self.assertIsInstance(app.screen, SubmitLogScreen)
            status = str(app.query_one("#status_bar", Label).render())
            self.assertIn("0 job(s) submitted, 2 failed", status)
            log_path = os.path.join(self.root, "submit.log")
            self.assertTrue(os.path.exists(log_path))
            with open(log_path) as f:
                content = f.read()
            self.assertIn("0 submitted, 2 failed", content)
            self.assertIn("error: invalid node name", content)
            app.screen.dismiss(None)

        app = ForgeBatchApp()
        await self._run_submit(app, failing_run, check)

    async def test_submit_success_opens_no_modal(self):
        from screens import SubmitLogScreen
        import types
        counter = {"id": 100}
        calls = []

        def ok_run(cmd, **kwargs):
            calls.append(list(cmd))
            counter["id"] += 1
            return types.SimpleNamespace(
                returncode=0, stdout=str(counter["id"]) + "\n", stderr="")

        def check(app, pilot):
            self.assertNotIsInstance(app.screen, SubmitLogScreen)
            for cmd in calls:
                self.assertTrue(os.path.isfile(cmd[-1]),
                                f"job file does not exist: {cmd[-1]}")
            log_path = os.path.join(self.root, "submit.log")
            self.assertTrue(os.path.exists(log_path))
            with open(log_path) as f:
                content = f.read()
            self.assertIn("2 submitted, 0 failed", content)

        app = ForgeBatchApp()
        await self._run_submit(app, ok_run, check)

    async def test_submit_log_modal_renders(self):
        from screens import SubmitLogScreen, format_submit_log
        from textual.widgets import Markdown
        log = [
            {"label": "job-a", "job_file": "/tmp/job.slurm",
             "cmd": ["sbatch", "--parsable", "/tmp/job.slurm"],
             "returncode": 1, "stdout": "", "stderr": "error: invalid node name\n",
             "error": "", "ok": False},
        ]
        text = format_submit_log(log)
        self.assertIn("0 submitted, 1 failed", text)
        self.assertIn("FAIL", text)
        self.assertIn("error: invalid node name", text)

        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(SubmitLogScreen(log))
            await pilot.pause()
            md = app.screen.query_one(Markdown)
            self.assertIn("FAIL", md.source)
            self.assertIn("error: invalid node name", md.source)
            app.screen.dismiss(None)
            await pilot.pause()

    async def test_running_pane_queries_scheduler(self):
        import types
        import asyncio
        import getpass
        from textual.widgets import Static, Checkbox
        import app as app_mod
        app = ForgeBatchApp()
        old_slurm = app_mod.use_slurm
        old_scheduler = app_mod.scheduler
        old_exec = asyncio.create_subprocess_exec
        app_mod.use_slurm = True
        app_mod.scheduler = "slurm"
        calls = []
        me = getpass.getuser()

        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            proc = types.SimpleNamespace()

            async def comm():
                return (("JOBID|USER|NAME|STATE|TIME|TIMELIMIT\n"
                         f"12345|{me}|job-55|RUNNING|00:01:00|20:00:00\n"
                         "12346|someone-else|job-56|PENDING|00:00:00|20:00:00\n").encode(),
                        b"")
            proc.communicate = comm
            return proc

        try:
            asyncio.create_subprocess_exec = fake_exec
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app._refresh_running()
                await pilot.pause()
                box = app.query_one("#running_only_user", Checkbox)
                self.assertTrue(box.value)
                text = _running_items_text(app)
                self.assertIn("12345", text)
                self.assertIn("job-55", text)
                self.assertNotIn("job-56", text)
                self.assertFalse(any("squeue" in c and "-u" in c for c in calls))
                box.value = False
                await pilot.pause()
                text = _running_items_text(app)
                self.assertIn("12346", text)
                self.assertIn("job-56", text)
        finally:
            app_mod.use_slurm = old_slurm
            app_mod.scheduler = old_scheduler
            asyncio.create_subprocess_exec = old_exec

    async def test_running_list_scrolls_when_many_jobs(self):
        import types
        import asyncio
        import getpass
        from textual.widgets import Static
        import app as app_mod
        app = ForgeBatchApp()
        old_slurm = app_mod.use_slurm
        old_scheduler = app_mod.scheduler
        old_exec = asyncio.create_subprocess_exec
        app_mod.use_slurm = True
        app_mod.scheduler = "slurm"
        me = getpass.getuser()
        body = "JOBID|USER|NAME|STATE|TIME|TIMELIMIT\n"
        for i in range(300):
            body += f"{10000 + i}|{me}|job-{i}|RUNNING|00:01:00|20:00:00\n"

        async def fake_exec(*cmd, **kwargs):
            proc = types.SimpleNamespace()

            async def comm():
                return (body.encode(), b"")
            proc.communicate = comm
            return proc

        try:
            asyncio.create_subprocess_exec = fake_exec
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                app._activate("running")
                await pilot.pause()
                await app._refresh_running()
                await pilot.pause()
                box = app.query_one("#running_only_user", Checkbox)
                box.value = False
                await pilot.pause()
                table = app.query_one("#running_list", DataTable)
                self.assertGreater(table.row_count, 0)
                self.assertTrue(table.is_scrollable)
                self.assertGreater(table.virtual_size.height, table.region.height)
                self.assertLess(len(table._nodes), table.row_count)
        finally:
            app_mod.use_slurm = old_slurm
            app_mod.scheduler = old_scheduler
            asyncio.create_subprocess_exec = old_exec

    async def test_running_pane_no_scheduler(self):
        from textual.widgets import Static
        import app as app_mod
        app = ForgeBatchApp()
        old_scheduler = app_mod.scheduler
        app_mod.scheduler = None
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app._refresh_running()
                await pilot.pause()
                text = str(app.query_one("#running_msg", Label).render())
                self.assertIn("No scheduler found", text)
        finally:
            app_mod.scheduler = old_scheduler

    def _seed_running(self):
        u = getpass.getuser()
        return [
            {"job_id": "1", "name": "alpha", "user": u, "state": "RUNNING",
             "elapsed_sec": 100, "limit_sec": 1000, "queue": ""},
            {"job_id": "2", "name": "beta", "user": u, "state": "PENDING",
             "elapsed_sec": 50, "limit_sec": None, "queue": ""},
            {"job_id": "3", "name": "gamma", "user": u, "state": "FAILED",
             "elapsed_sec": 200, "limit_sec": 400, "queue": "batch"},
        ]

    async def test_running_filter_filters_rows(self):
        app = ForgeBatchApp()
        app._running_rows = self._seed_running()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._render_running()
            await pilot.pause()
            filt = app.query_one("#running_filter", Input)
            filt.value = "ga"
            app._render_running()
            await pilot.pause()
            text = _running_items_text(app)
            self.assertIn("gamma", text)
            self.assertNotIn("alpha", text)
            self.assertNotIn("beta", text)

    async def test_running_sort_by_name_and_time(self):
        app = ForgeBatchApp()
        app._running_rows = self._seed_running()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            sort = app.query_one("#running_sort", Select)
            sort.value = "name"
            app._render_running()
            await pilot.pause()
            text = _running_items_text(app)
            self.assertLess(text.index("alpha"), text.index("beta"))
            self.assertLess(text.index("beta"), text.index("gamma"))
            sort.value = "time"
            app._render_running()
            await pilot.pause()
            text = _running_items_text(app)
            self.assertLess(text.index("beta"), text.index("alpha"))
            self.assertLess(text.index("alpha"), text.index("gamma"))

    async def test_running_row_shows_progress_and_queue(self):
        app = ForgeBatchApp()
        app._running_rows = self._seed_running()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._render_running()
            await pilot.pause()
            text = _running_items_text(app)
            self.assertIn("10%", text)
            self.assertIn("batch", text)

    async def test_running_cancel_calls_scancel(self):
        app = ForgeBatchApp()
        app._running_rows = self._seed_running()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._render_running()
            await pilot.pause()
            table = app.query_one("#running_list", DataTable)
            table.focus()
            table.cursor_coordinate = Coordinate(0, 0)
            rec = app._selected_job()
            self.assertIsNotNone(rec)
            calls = []

            async def fake_capture(cmd):
                calls.append(list(cmd))
                return ""

            async def fake_refresh():
                return None
            app._capture = fake_capture
            app._refresh_running = fake_refresh
            await app._do_cancel(rec, True)
            self.assertEqual(calls, [["scancel", rec["job_id"]]])

    async def test_running_details_pushes_screen(self):
        app = ForgeBatchApp()
        app._running_rows = self._seed_running()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._render_running()
            await pilot.pause()
            table = app.query_one("#running_list", DataTable)
            table.focus()
            table.cursor_coordinate = Coordinate(2, 0)
            rec = app._selected_job()
            self.assertEqual(rec["job_id"], "3")

            async def fake_capture(cmd):
                return "JobId=3\nName=gamma\nState=FAILED"
            app._capture = fake_capture
            event = types.SimpleNamespace(button=types.SimpleNamespace(id="job_details"))
            await app.on_button_pressed(event)
            await pilot.pause()
            self.assertIsInstance(app.screen, JobDetailsScreen)

    async def test_preview_dir_node_does_not_show_none(self):
        import types
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            app._rows = [data]
            await app._rebuild_rows()
            await pilot.pause()
            app.collect_settings()
            app.action_preview()
            await pilot.pause()
            tree = app.query_one("#job_tree")
            variant_node = tree.root.children[0]
            self.assertNotIn("content", variant_node.data)
            app.on_tree_node_selected(types.SimpleNamespace(node=variant_node))
            await pilot.pause()
            viewer = app.query_one("#job_viewer", TextArea)
            self.assertEqual(viewer.text, "")

    async def test_settings_input_schedules_autosave(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            grid[0].value = self.root
            await pilot.pause()
            self.assertEqual(app.settings["job_root"], self.root)
            self.assertIsNotNone(app._autosave_timer)

    async def test_apply_preset_refreshes_ui(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.apply_preset({
                "job_root": self.root,
                "email": "bob@example.com",
                "job_body": "echo body\n",
                "included_files": [{"path": data}],
            })
            await pilot.pause()
            grid = app.query_one("#settings_grid").query(Input).nodes
            self.assertEqual(grid[0].value, self.root)
            self.assertEqual(grid[1].value, "")
            self.assertEqual(grid[2].value, "bob@example.com")
            self.assertIn(data, [r.get("path", r) if isinstance(r, dict) else r for r in app._rows])
            tabs = [b.label for b in app.query_one("#file_tabs").query("Button")]
            self.assertTrue(any("data.txt" in str(t) for t in tabs))
            header = app.query_one("#job_header_view", TextArea).text
            self.assertIn(self.root, header)
            self.assertEqual(app.query_one("#job_body_editor", TextArea).text,
                             "echo body\n")

    async def test_preset_save_delete_persist_across_save(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = PresetsScreen(app, app.settings)
            await app.push_screen(screen)
            await pilot.pause()
            name_input = screen.query_one("#preset_name", Input)

            name_input.value = "p1"
            screen.on_button_pressed(types.SimpleNamespace(
                button=types.SimpleNamespace(id="save_preset")))
            await pilot.pause()
            self.assertIn("p1", config_mod.read_config()["presets"])
            self.assertIn("p1", app.cfg["presets"])

            app.action_save()
            await pilot.pause()
            self.assertIn("p1", config_mod.read_config()["presets"],
                          "saving must not drop a freshly saved preset")

            name_input.value = "p1"
            screen.on_button_pressed(types.SimpleNamespace(
                button=types.SimpleNamespace(id="del_preset")))
            await pilot.pause()
            self.assertNotIn("p1", config_mod.read_config()["presets"])
            self.assertNotIn("p1", app.cfg["presets"])

            app.action_save()
            await pilot.pause()
            self.assertNotIn("p1", config_mod.read_config()["presets"],
                             "saving must not resurrect a deleted preset")

    async def test_preset_overwrite_replaces_settings(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = PresetsScreen(app, {"job_root": "/a", "email": "x@y.z"})
            await app.push_screen(screen)
            await pilot.pause()
            name_input = screen.query_one("#preset_name", Input)
            name_input.value = "p1"
            screen.on_button_pressed(types.SimpleNamespace(
                button=types.SimpleNamespace(id="save_preset")))
            await pilot.pause()
            screen.current = {"job_root": "/b", "email": "q@r.s"}
            screen.on_button_pressed(types.SimpleNamespace(
                button=types.SimpleNamespace(id="save_preset")))
            await pilot.pause()
            saved = config_mod.read_config()["presets"]["p1"]
            self.assertEqual(saved["email"], "q@r.s")
            self.assertEqual(saved["job_root"], "/b")

    async def test_preset_delete_unknown_is_safe(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = PresetsScreen(app, app.settings)
            await app.push_screen(screen)
            await pilot.pause()
            name_input = screen.query_one("#preset_name", Input)
            name_input.value = "ghost"
            screen.on_button_pressed(types.SimpleNamespace(
                button=types.SimpleNamespace(id="del_preset")))
            await pilot.pause()
            self.assertNotIn("ghost", config_mod.read_config()["presets"])

    async def test_preset_load_sets_status_bar(self):
        os.makedirs(self.root, exist_ok=True)
        config_mod.write_config({
            "last": {}, "selected_preset": "", "presets": {
                "p1": {"job_root": self.root, "email": "bob@example.com"}}})
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = PresetsScreen(app, app.settings)
            await app.push_screen(screen)
            await pilot.pause()
            screen._load("p1")
            await pilot.pause()
            self.assertEqual(str(app.query_one("#status_bar", Label).render()),
                             "Loaded preset 'p1'")
            self.assertEqual(app.query_one("#email", Input).value,
                             "bob@example.com")

    async def test_cli_preset_loads_on_mount(self):
        import app as app_mod
        os.makedirs(self.root, exist_ok=True)
        config_mod.write_config({
            "last": {}, "selected_preset": "", "presets": {
                "p1": {"job_root": self.root, "email": "bob@example.com"}}})
        app = app_mod.ForgeBatchApp(preset="p1")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one("#email", Input).value,
                             "bob@example.com")
            self.assertEqual(app.query_one("#job_root", Input).value, self.root)
            self.assertEqual(str(app.query_one("#status_bar", Label).render()),
                             "Loaded preset 'p1'")

    async def test_cli_preset_not_found_raises(self):
        import app as app_mod
        config_mod.write_config({"last": {}, "selected_preset": "", "presets": {}})
        with self.assertRaises(SystemExit):
            app_mod._resolve_preset("ghost")
        config_mod.write_config({
            "last": {}, "selected_preset": "", "presets": {
                "p1": {"job_root": self.root}}})
        self.assertEqual(app_mod._resolve_preset("p1")["job_root"], self.root)

    async def test_import_populates_email_settings_and_job_body(self):
        import builder
        src = os.path.join(self.tmp, "job.slurm")
        with open(src, "w") as f:
            f.write("#!/bin/sh\n"
                    "#SBATCH --job-name=X-1\n"
                    "#SBATCH --mail-user=bob@example.com\n"
                    "#SBATCH --qos=qos_gpu-t3\n"
                    "module purge\n"
                    "python train.py\n")
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._import_job_result(src)
            await pilot.pause()
            self.assertEqual(app.settings["email"], "bob@example.com")
            self.assertEqual(app.query_one("#email", Input).value, "bob@example.com")
            body = app.query_one("#job_body_editor", TextArea).text
            self.assertIn("#SBATCH --qos=qos_gpu-t3", body)
            self.assertNotIn("--job-name", body)
            self.assertNotIn("--mail-user", body)
            self.assertIn("module purge", body)
            self.assertIn("python train.py", body)
            self.assertEqual(app.settings["job_body"], body)
            app.collect_settings()
            variants, err, _ = builder.build_jobs(app.settings)
            self.assertIsNone(err)
            job = variants[0]["job"]
            self.assertIn("#SBATCH --mail-user=bob@example.com", job)
            self.assertIn("#SBATCH --qos=qos_gpu-t3", job)
            self.assertIn("python train.py", job)

    async def test_import_from_text_populates_fields(self):
        import builder
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._import_job_text("#!/bin/sh\n"
                                 "#SBATCH --job-name=X-1\n"
                                 "#SBATCH --mail-user=bob@example.com\n"
                                 "#SBATCH --qos=qos_gpu-t3\n"
                                 "module purge\n"
                                 "python train.py\n")
            await pilot.pause()
            self.assertEqual(app.settings["email"], "bob@example.com")
            body = app.query_one("#job_body_editor", TextArea).text
            self.assertIn("#SBATCH --qos=qos_gpu-t3", body)
            self.assertNotIn("--job-name", body)
            self.assertIn("module purge", body)
            self.assertIn("python train.py", body)
            app.collect_settings()
            variants, err, _ = builder.build_jobs(app.settings)
            self.assertIsNone(err)
            self.assertIn("#SBATCH --mail-user=bob@example.com", variants[0]["job"])
            self.assertIn("python train.py", variants[0]["job"])

    async def test_clear_job_fields_resets_settings(self):
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.settings["job_root"] = "/tmp/root"
            app.settings["email"] = "a@b.c"
            app.settings["job_body"] = "#SBATCH --qos=q\n"
            app.query_one("#job_body_editor", TextArea).load_text("#SBATCH --qos=q\n")
            await pilot.pause()
            app._clear_job_fields()
            await pilot.pause()
            self.assertEqual(app.settings["job_root"], os.getcwd())
            self.assertEqual(app.settings["email"], "")
            self.assertEqual(app.settings["job_body"], "cd {{JOBDIR}}\n")
            self.assertEqual(app.query_one("#job_body_editor", TextArea).text,
                             "cd {{JOBDIR}}\n")
            self.assertEqual(app.query_one("#email", Input).value, "")

    async def test_top_bar_right_side_tabs(self):
        from textual.widgets import Button, Static
        app = ForgeBatchApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            actions = app.query_one("#actions")
            widgets = [w.id or w.__class__.__name__ for w in actions.children]
            self.assertEqual(widgets,
                             ["help", "presets",
                              "actions_spacer", "scheduler_info",
                              "tab_jobs"])
            self.assertEqual(app.query_one("#tab_jobs", Button).label,
                             "Jobs queue")
            self.assertEqual(app.query_one("#tab_preview", Button).label,
                             "Preview job(s)")
            self.assertIs(app.query_one("#tab_preview", Button).parent,
                          app.query_one("#tab_bar"))
            app._activate("running")
            await pilot.pause()
            self.assertIn("active", app.query_one("#tab_jobs", Button).classes)
            self.assertNotIn("active", app.query_one("#tab_preview", Button).classes)


if __name__ == "__main__":
    unittest.main()
