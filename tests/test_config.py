"""Unit tests for config helpers (config.py).

Run from the forgebatch/ repo root:
    python3 tests/test_config.py
or:
    python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import config  # noqa: E402


class NextJobIdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_root_returns_1_without_creating_dir(self):
        root = os.path.join(self.tmp, "does", "not", "exist")
        self.assertEqual(config.next_job_id(root), "1")
        self.assertFalse(os.path.exists(root))

    def test_missing_parent_does_not_crash(self):
        # regression: preview on a job_root whose parent is missing must not
        # raise (next_job_id used to os.mkdir the root and crash).
        root = os.path.join(self.tmp, "missing_parent", "jobs")
        self.assertEqual(config.next_job_id(root), "1")

    def test_empty_root_returns_1(self):
        root = os.path.join(self.tmp, "jobs")
        os.makedirs(root)
        self.assertEqual(config.next_job_id(root), "1")

    def test_increments_over_numeric_dirs_and_ignores_others(self):
        root = os.path.join(self.tmp, "jobs")
        os.makedirs(root)
        for d in ("1", "2", "3"):
            os.makedirs(os.path.join(root, d))
        os.makedirs(os.path.join(root, "not_a_number"))
        with open(os.path.join(root, "3", "job.slurm"), "w") as f:
            f.write("x")
        self.assertEqual(config.next_job_id(root), "4")


if __name__ == "__main__":
    unittest.main()
