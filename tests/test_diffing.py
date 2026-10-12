"""Unit tests for the diff-based edit storage (diffing.py).

Run from the forgebatch/ repo root:
    python3 tests/test_diffing.py
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import diffing  # noqa: E402


class ComputePatchTest(unittest.TestCase):
    def test_empty_base_yields_additions(self):
        p = diffing.compute_patch("", "hello\n")
        self.assertTrue(p.startswith("--- "))
        text, conflicts = diffing.apply_patch("", p)
        self.assertEqual(text, "hello\n")
        self.assertFalse(conflicts)

    def test_roundtrip_edits(self):
        base = "line1\nline2\nline3\nline4\n"
        edited = "line1\nCHANGED\nline3\nline4\nEXTRA\n"
        p = diffing.compute_patch(base, edited)
        text, conflicts = diffing.apply_patch(base, p)
        self.assertEqual(text, edited)
        self.assertFalse(conflicts)

    def test_no_change_empty_patch(self):
        base = "a\nb\n"
        p = diffing.compute_patch(base, base)
        self.assertEqual(p, "")
        text, conflicts = diffing.apply_patch(base, p)
        self.assertEqual(text, base)
        self.assertFalse(conflicts)


class IsPatchTest(unittest.TestCase):
    def test_patch_detection(self):
        p = diffing.compute_patch("a\n", "b\n")
        self.assertTrue(diffing.is_patch(p))
        self.assertFalse(diffing.is_patch("plain full text\n"))

    def test_non_string(self):
        self.assertFalse(diffing.is_patch(None))
        self.assertFalse(diffing.is_patch(42))


class ApplyPatchMergeTest(unittest.TestCase):
    def test_apply_to_changed_source(self):
        # patch made against an old base, applied to a newer base
        old = "keep\nold-a\nkeep\nold-b\nkeep\n"
        edited = "keep\nNEW-A\nkeep\nold-b\nkeep\n"
        p = diffing.compute_patch(old, edited)
        newer = "keep\nCHANGED-A\nkeep\nold-b\nkeep\n"
        text, conflicts = diffing.apply_patch(newer, p)
        # the changed line A is preserved, the edit to it becomes a conflict
        self.assertTrue(conflicts)
        self.assertIn("NEW-A", text)
        self.assertIn("CHANGED-A", text)

    def test_conflict_does_not_duplicate_remaining_file(self):
        old = "keep\nold-a\nkeep\nold-b\nkeep\n"
        edited = "keep\nNEW-A\nkeep\nNEW-B\nkeep\n"
        p = diffing.compute_patch(old, edited)
        newer = "keep\nCHANGED-A\nkeep\nCHANGED-B\nkeep\n"
        text, conflicts = diffing.apply_patch(newer, p)
        self.assertTrue(conflicts)
        # the real base lines must not be duplicated after the conflict markers
        self.assertEqual(text.count("CHANGED-A"), 1)
        self.assertEqual(text.count("CHANGED-B"), 1)
        self.assertIn("NEW-A", text)
        self.assertIn("NEW-B", text)

    def test_apply_clean_when_source_matches(self):
        base = "x\n"
        edited = "y\n"
        p = diffing.compute_patch(base, edited)
        text, conflicts = diffing.apply_patch("x\n", p)
        self.assertFalse(conflicts)
        self.assertEqual(text, "y\n")

    def test_no_trailing_newline_round_trips_without_conflict(self):
        # files written without a trailing newline (e.g. json.dump) used to
        # produce a malformed patch whose '-x+y' records ran together, so the
        # edit always conflicted and emitted conflict markers.
        base = '{"lr": 0}'
        edited = '{"lr": []}'
        p = diffing.compute_patch(base, edited)
        self.assertIn("\n-", p)
        text, conflicts = diffing.apply_patch(base, p)
        self.assertFalse(conflicts)
        self.assertEqual(text, '{"lr": []}\n')


class ResolveEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_legacy_full_text(self):
        text, binary, conflicts = diffing.resolve_edit("/nonexistent", "legacy content\n")
        self.assertEqual(text, "legacy content\n")
        self.assertFalse(binary)
        self.assertFalse(conflicts)

    def test_patch_applied_to_file(self):
        path = os.path.join(self.tmp, "f.txt")
        with open(path, "w") as f:
            f.write("a\nb\nc\n")
        p = diffing.compute_patch("a\nb\nc\n", "a\nB\nc\n")
        text, binary, conflicts = diffing.resolve_edit(path, p)
        self.assertEqual(text, "a\nB\nc\n")
        self.assertFalse(binary)
        self.assertFalse(conflicts)

    def test_missing_file_all_additions(self):
        path = os.path.join(self.tmp, "nope.txt")
        p = diffing.compute_patch("", "new\n")
        text, binary, conflicts = diffing.resolve_edit(path, p)
        self.assertEqual(text, "new\n")

    def test_none(self):
        text, binary, conflicts = diffing.resolve_edit("/x", None)
        self.assertIsNone(text)
        self.assertFalse(binary)
        self.assertFalse(conflicts)


if __name__ == "__main__":
    unittest.main()
