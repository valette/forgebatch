"""Unit tests for modal screens (screens.py), focusing on the FileBrowserScreen
double-click-to-select handler.

Run from the forgebatch/ repo root:
    python3 tests/test_screens.py
or:
    python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from app import ForgeBatchApp  # noqa: E402
from screens import (  # noqa: E402
    BrowseTree, ConfirmScreen, FileBrowserScreen, JobDetailsScreen, _load_help,
)
from textual.widgets import Label, Static  # noqa: E402


def _click(chain, meta=None):
    return types.SimpleNamespace(
        chain=chain, style=types.SimpleNamespace(meta=meta or {}))


def _node(path):
    return types.SimpleNamespace(
        data=types.SimpleNamespace(path=Path(path), is_dir=lambda: os.path.isdir(path)))


class FileBrowserDoubleClickTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.file = os.path.join(self.tmp, "a.json")
        with open(self.file, "w") as f:
            f.write("{}\n")
        self.dir = os.path.join(self.tmp, "sub")
        os.makedirs(self.dir)

    async def _open(self):
        app = ForgeBatchApp()
        await app.run_test(size=(120, 40))
        return app

    async def test_single_click_does_not_dismiss(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            screen = FileBrowserScreen(self.tmp)
            await app.push_screen(screen)
            screen.dirtree = types.SimpleNamespace(
                cursor_node=_node(self.file),
                get_node_at_line=lambda line: _node(self.file))
            screen.on_click(_click(1))
            self.assertIs(app.screen, screen)

    async def test_double_click_falls_back_to_cursor_node(self):
        # No style.meta line -> must fall back to the cursor node and select.
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            results = []
            screen = FileBrowserScreen(self.tmp)
            await app.push_screen(screen, callback=results.append)
            screen.dirtree = types.SimpleNamespace(
                cursor_node=_node(self.file),
                get_node_at_line=lambda line: None)
            screen.on_click(_click(2))
            await pilot.pause()
            self.assertIsNot(app.screen, screen)
            self.assertEqual(results, [self.file])

    async def test_double_click_resolves_via_line(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            results = []
            screen = FileBrowserScreen(self.tmp)
            await app.push_screen(screen, callback=results.append)
            screen.dirtree = types.SimpleNamespace(
                cursor_node=_node(self.dir),
                get_node_at_line=lambda line: _node(self.file))
            screen.on_click(_click(2, meta={"line": 3}))
            await pilot.pause()
            self.assertIsNot(app.screen, screen)
            self.assertEqual(results, [self.file])

    async def test_double_click_on_directory_selects_it(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            results = []
            screen = FileBrowserScreen(self.tmp)
            await app.push_screen(screen, callback=results.append)
            screen.dirtree = types.SimpleNamespace(
                cursor_node=_node(self.dir),
                get_node_at_line=lambda line: _node(self.dir))
            screen.on_click(_click(2))
            await pilot.pause()
            self.assertIsNot(app.screen, screen)
            self.assertEqual(results, [self.dir])

    async def test_action_choose_on_directory_selects_node_not_parent(self):
        # Choosing a directory must return the directory itself, not the
        # currently-displayed parent (self.dirtree.path).
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            results = []
            screen = FileBrowserScreen(self.tmp)
            await app.push_screen(screen, callback=results.append)
            screen.dirtree = types.SimpleNamespace(
                cursor_node=_node(self.dir),
                path=Path(self.tmp))
            screen.action_choose()
            await pilot.pause()
            self.assertIsNot(app.screen, screen)
            self.assertEqual(results, [self.dir])

    async def test_action_choose_with_no_cursor_selects_displayed_dir(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            results = []
            screen = FileBrowserScreen(self.tmp)
            await app.push_screen(screen, callback=results.append)
            screen.dirtree = types.SimpleNamespace(
                cursor_node=None, path=Path(self.tmp))
            screen.action_choose()
            await pilot.pause()
            self.assertIsNot(app.screen, screen)
            self.assertEqual(results, [self.tmp])


class BrowseTreeFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.job = os.path.join(self.tmp, "job.slurm")
        self.other = os.path.join(self.tmp, "data.txt")
        self.sub = os.path.join(self.tmp, "sub")
        open(self.job, "w").close()
        open(self.other, "w").close()
        os.makedirs(self.sub)

    def _paths(self):
        return [Path(self.job), Path(self.other), Path(self.sub)]

    def test_unfiltered_shows_everything(self):
        tree = BrowseTree(self.tmp)
        self.assertEqual(set(tree.filter_paths(self._paths())),
                         set(self._paths()))

    def test_filter_hides_nonmatching_files_keeps_dirs(self):
        tree = BrowseTree(self.tmp, filter_ext=".slurm")
        shown = list(tree.filter_paths(self._paths()))
        names = {p.name for p in shown}
        self.assertIn("job.slurm", names)
        self.assertIn("sub", names)
        self.assertNotIn("data.txt", names)

    def test_filter_matches_case_insensitively(self):
        tree = BrowseTree(self.tmp, filter_ext=".SLURM")
        names = {p.name for p in tree.filter_paths(self._paths())}
        self.assertIn("job.slurm", names)
        self.assertNotIn("data.txt", names)


class HelpTextTest(unittest.TestCase):
    def test_load_help_reads_help_md(self):
        text = _load_help()
        self.assertIn("forgebatch", text)
        self.assertIn("{{JOBID}}", text)
        self.assertIn("{{JOBDIR}}", text)
        self.assertIn("Parallelism limit", text)

    def test_load_help_documents_job_body_sweep(self):
        text = _load_help()
        self.assertIn("[[", text)
        self.assertIn("--lr [[0.001, 0.01, 0.1]]", text)
        self.assertIn("Job-body (script) sweeps", text)


class NewModalScreensTest(unittest.IsolatedAsyncioTestCase):
    async def test_job_details_screen_shows_text(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            await pilot.app.push_screen(JobDetailsScreen("42", "JobId=42\nName=job-x"))
            self.assertIn("Job details: 42",
                          str(pilot.app.screen.query_one("#modal_title", Label).render()))
            body = pilot.app.screen.query_one("#details_text", Static).render()
            self.assertIn("Name=job-x", str(body))

    async def test_confirm_ok_dismisses_true(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            result = {}
            await pilot.app.push_screen(
                ConfirmScreen("Cancel job", "Cancel job 42?"),
                callback=lambda v: result.setdefault("v", v))
            self.assertIn("Cancel job 42?",
                          str(pilot.app.screen.query_one("#confirm_text", Label).render()))
            await pilot.click("#ok_btn")
            self.assertTrue(result["v"])

    async def test_confirm_cancel_dismisses_none(self):
        async with ForgeBatchApp().run_test(size=(120, 40)) as pilot:
            result = {}
            await pilot.app.push_screen(
                ConfirmScreen("Cancel job", "Cancel job 42?"),
                callback=lambda v: result.setdefault("v", v))
            await pilot.click("#cancel_btn")
            self.assertIsNone(result["v"])


if __name__ == "__main__":
    unittest.main()
