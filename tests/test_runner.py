"""Unit tests for the Textual-free submission helpers (runner.py).

Run from the forgebatch/ repo root:
    python3 tests/test_runner.py
or:
    python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import runner  # noqa: E402


def _variant(i):
    return {"label": f"job-{i}", "job_file": f"/jobs/{i}/job.slurm",
            "job_path": f"/jobs/{i}", "job": "", "files": {}}


class RunSubmissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_parallel_limits_serial_chains_with_deps(self):
        variants = [_variant(i) for i in range(3)]
        calls = []
        returned = []
        counter = {"id": 100}

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            counter["id"] += 1
            returned.append(str(counter["id"]))
            return types.SimpleNamespace(
                returncode=0, stdout=str(counter["id"]) + "\n", stderr="")

        log, submitted, failed = runner.run_submission(
            variants, self.tmp, 1, "slurm", True, run=fake_run)

        self.assertEqual((submitted, failed), (3, 0))
        self.assertEqual(len(calls), 3)
        self.assertEqual([a for a in calls[0] if a.startswith("--dependency=")], [])
        deps = [a for a in calls[1] if a.startswith("--dependency=")]
        self.assertEqual(deps, ["--dependency=afterok:" + returned[0]])
        self.assertTrue(all("--parsable" in c for c in calls))

    def test_failure_recorded_without_raising(self):
        variants = [_variant(0), _variant(1)]

        def failing_run(cmd, **kwargs):
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="error: bad node\n")

        log, submitted, failed = runner.run_submission(
            variants, self.tmp, 1, "slurm", True, run=failing_run)
        self.assertEqual((submitted, failed), (0, 2))
        self.assertEqual(len(log), 2)
        self.assertIn("error: bad node", log[0]["stderr"])

    def test_oserror_recorded(self):
        def boom(cmd, **kwargs):
            raise OSError("no sbatch")

        log, submitted, failed = runner.run_submission(
            [_variant(0)], self.tmp, 1, "slurm", True, run=boom)
        self.assertEqual((submitted, failed), (0, 1))
        self.assertIn("no sbatch", log[0]["error"])


class ParseRunningOutputTest(unittest.TestCase):
    def test_slurm_rows(self):
        text = ("JOBID|USER|NAME|STATE|TIME|TIMELIMIT\n"
                "12345|alice|job-55|RUNNING|00:01:00|20:00:00\n"
                "12346|bob|job-56|PENDING|00:00:00|20:00:00\n")
        rows = runner.parse_running_output(text, use_slurm=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["job_id"], "12345")
        self.assertEqual(rows[0]["user"], "alice")
        self.assertEqual(rows[0]["name"], "job-55")
        self.assertEqual(rows[0]["state"], "RUNNING")
        self.assertEqual(rows[0]["elapsed_sec"], 60)
        self.assertEqual(rows[0]["limit_sec"], 20 * 3600)
        self.assertEqual(rows[1]["state"], "PENDING")

    def test_slurm_unlimited_limit_is_none(self):
        text = ("JOBID|USER|NAME|STATE|TIME|TIMELIMIT\n"
                "1|alice|job|RUNNING|00:00:05|UNLIMITED\n")
        rows = runner.parse_running_output(text, use_slurm=True)
        self.assertIsNone(rows[0]["limit_sec"])
        self.assertEqual(rows[0]["elapsed_sec"], 5)

    def test_slurm_empty(self):
        self.assertEqual(runner.parse_running_output("", use_slurm=True), [])

    def test_pbs_rows_from_xml(self):
        text = ('<?xml version="1.0"?>\n<Data>\n'
                "  <Job>\n"
                "    <Job_Id>4711.a-very-long-server-hostname.cec</Job_Id>\n"
                "    <Job_Name>a_very_long_job_name_that_qstat_would_truncate</Job_Name>\n"
                "    <Job_Owner>alice@server</Job_Owner>\n"
                "    <resources_used><walltime>00:00:02</walltime></resources_used>\n"
                "    <Resource_List><walltime>02:00:00</walltime></Resource_List>\n"
                "    <job_state>R</job_state>\n"
                "    <queue>batch</queue>\n"
                "  </Job>\n</Data>\n")
        rows = runner.parse_running_output(text, use_slurm=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "4711.a-very-long-server-hostname.cec")
        self.assertEqual(rows[0]["name"],
                         "a_very_long_job_name_that_qstat_would_truncate")
        self.assertEqual(rows[0]["user"], "alice")
        self.assertEqual(rows[0]["state"], "R")
        self.assertEqual(rows[0]["elapsed_sec"], 2)
        self.assertEqual(rows[0]["limit_sec"], 2 * 3600)
        self.assertEqual(rows[0]["queue"], "batch")

    def test_pbs_queued_job_elapsed_zero_with_limit(self):
        text = ('<?xml version="1.0"?>\n<Data>\n'
                "  <Job>\n"
                "    <Job_Id>5093[].host.example</Job_Id>\n"
                "    <Job_Name>queued_job</Job_Name>\n"
                "    <Job_Owner>bob@host</Job_Owner>\n"
                "    <Resource_List><walltime>00:30:00</walltime></Resource_List>\n"
                "    <job_state>Q</job_state>\n"
                "    <queue>short</queue>\n"
                "  </Job>\n</Data>\n")
        rows = runner.parse_running_output(text, use_slurm=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "Q")
        self.assertEqual(rows[0]["elapsed_sec"], 0)
        self.assertEqual(rows[0]["limit_sec"], 30 * 60)

    def test_pbs_job_without_walltime_has_no_limit(self):
        text = ('<?xml version="1.0"?>\n<Data>\n'
                "  <Job>\n"
                "    <Job_Id>1.host</Job_Id>\n"
                "    <Job_Name>held_job</Job_Name>\n"
                "    <Job_Owner>carol@host</Job_Owner>\n"
                "    <job_state>H</job_state>\n"
                "    <queue>long</queue>\n"
                "  </Job>\n</Data>\n")
        rows = runner.parse_running_output(text, use_slurm=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["elapsed_sec"], 0)
        self.assertIsNone(rows[0]["limit_sec"])

    def test_pbs_rows_from_xml_no_jobs(self):
        self.assertEqual(runner.parse_running_output(
            '<?xml version="1.0"?>\n<Data>\n</Data>\n', use_slurm=False), [])

    def test_pbs_rows_falls_back_to_fixed_columns(self):
        text = ("Job id            Name             User              Time Use S Queue\n"
                "5093099[].node.cluster.local job_a alice 0 Q short\n"
                "------------------------- ---------------- --------------- -------- - -----\n")
        rows = runner.parse_running_output(text, use_slurm=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "5093099[].node.cluster.local")
        self.assertEqual(rows[0]["name"], "job_a")
        self.assertEqual(rows[0]["state"], "Q")
        self.assertEqual(rows[0]["queue"], "short")


class RunningHelpersTest(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(runner.parse_duration("20:00:00"), 20 * 3600)
        self.assertEqual(runner.parse_duration("1-12:30:15"),
                         86400 + 12 * 3600 + 30 * 60 + 15)
        self.assertEqual(runner.parse_duration("00:00:02"), 2)
        self.assertIsNone(runner.parse_duration(""))
        self.assertIsNone(runner.parse_duration("UNLIMITED"))
        self.assertIsNone(runner.parse_duration("--"))
        self.assertIsNone(runner.parse_duration("garbage"))

    def test_state_category(self):
        self.assertEqual(runner.state_category("RUNNING"), "running")
        self.assertEqual(runner.state_category("R"), "running")
        self.assertEqual(runner.state_category("PENDING"), "pending")
        self.assertEqual(runner.state_category("Q"), "pending")
        self.assertEqual(runner.state_category("COMPLETED"), "completed")
        self.assertEqual(runner.state_category("FAILED"), "failed")
        self.assertEqual(runner.state_category("E"), "failed")
        self.assertEqual(runner.state_category("CANCELLED"), "cancelled")
        self.assertEqual(runner.state_category("BOGUS"), "unknown")
        self.assertEqual(runner.state_category(""), "unknown")

    def test_progress_cells(self):
        pct, bar = runner.progress_cells(1800, 3600, width=10)
        self.assertEqual(pct, 50)
        self.assertEqual(len(bar), 10)
        self.assertEqual(bar.count("█"), 5)
        self.assertEqual(bar.count("░"), 5)
        pct, bar = runner.progress_cells(300, 3600, width=10)
        self.assertEqual(bar.count("█"), 1)
        pct, bar = runner.progress_cells(3600, 3600, width=10)
        self.assertEqual(pct, 100)
        self.assertEqual(bar.count("░"), 0)
        pct, bar = runner.progress_cells(1, None, width=10)
        self.assertIsNone(pct)
        self.assertEqual(bar, " " * 10)

    def test_cancel_and_detail_cmd(self):
        self.assertEqual(runner.cancel_cmd("slurm", "42"), ["scancel", "42"])
        self.assertEqual(runner.cancel_cmd("pbs", "42"), ["qdel", "42"])
        self.assertEqual(runner.detail_cmd(True, "42"), ["scontrol", "show", "job", "42"])
        self.assertEqual(runner.detail_cmd(False, "42"), ["qstat", "-f", "42"])


if __name__ == "__main__":
    unittest.main()
