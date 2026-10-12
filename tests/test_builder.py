"""Unit tests for the pure job-generation logic (builder.py).

Run from the forgebatch/ repo root:
    python3 tests/test_builder.py
or:
    python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import builder  # noqa: E402
import config  # noqa: E402


def _json_obj_path(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w") as f:
        json.dump(obj, f)
    return p


class BuildJobsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _fields(self, included, edits=None, job_body=None):
        return {
            "job_root": os.path.join(self.tmp, "jobs"),
            "email": "alice@example.com",
            "job_body": job_body or "",
            "included_files": included,
            "file_edits": edits or {},
        }

    def test_unreadable_included_file_adds_warning(self):
        # A directory path "exists" but cannot be read as a file, so it must be
        # reported as a warning rather than silently dropped (regression).
        f1 = _json_obj_path(self.tmp, "a.json", {"x": 1})
        unreadable = os.path.join(self.tmp, "subdir")
        os.makedirs(unreadable)
        fields = self._fields([
            {"path": f1},
            {"path": unreadable},
        ])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertTrue(any(unreadable in w for w in warnings), warnings)

    def test_missing_included_file_is_skipped_without_warning(self):
        f1 = _json_obj_path(self.tmp, "a.json", {"x": 1})
        fields = self._fields([
            {"path": f1},
            {"path": os.path.join(self.tmp, "nope.json")},
        ])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertEqual(warnings, [])

    def test_build_jobs_returns_error_when_job_root_missing(self):
        fields = self._fields([])
        fields["job_root"] = ""
        variants, err, warnings = builder.build_jobs(fields)
        self.assertEqual(variants, [])
        self.assertIn("jobs root is not set", err)
        self.assertEqual(warnings, [])

    def test_included_dir_adds_dir_entry_per_variant(self):
        src = os.path.join(self.tmp, "assets")
        os.makedirs(os.path.join(src, "nested"))
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("hi")
        with open(os.path.join(src, "nested", "g.txt"), "w") as f:
            f.write("yo")
        fields = self._fields([{"path": _json_obj_path(self.tmp, "a.json", {"x": 1})}])
        fields["included_dirs"] = [{"path": src, "mode": "copy"}]
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertEqual(warnings, [])
        v = variants[0]
        self.assertEqual(len(v["dirs"]), 1)
        d = v["dirs"][0]
        self.assertEqual(d["path"], src)
        self.assertEqual(d["basename"], "assets")
        self.assertEqual(d["kind"], "dir")
        self.assertEqual(d["mode"], "copy")
        # directories never become swept/edited file entries
        self.assertNotIn("assets", v["files"])
        self.assertIn("a.json", v["files"])

    def test_included_dir_defaults_to_symlink(self):
        src = os.path.join(self.tmp, "assets")
        os.makedirs(src)
        fields = self._fields([])
        fields["included_dirs"] = [{"path": src}]
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(variants[0]["dirs"][0]["mode"], "symlink")

    def test_included_dir_missing_adds_warning(self):
        fields = self._fields([])
        fields["included_dirs"] = [{"path": os.path.join(self.tmp, "nope")}]
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(variants[0]["dirs"], [])
        self.assertTrue(any("nope" in w for w in warnings), warnings)

    def test_symlinked_file_goes_to_file_links_not_files(self):
        f1 = _json_obj_path(self.tmp, "a.json", {"x": 1})
        txt = os.path.join(self.tmp, "notes.txt")
        with open(txt, "w") as f:
            f.write("hello\n")
        fields = self._fields([{"path": f1}, {"path": txt, "mode": "symlink"}])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertEqual(warnings, [])
        v = variants[0]
        # symlinked file is not copied into files, not read, not edited
        self.assertNotIn("notes.txt", v["files"])
        self.assertIn("a.json", v["files"])
        self.assertEqual(v["file_links"], [{"path": txt, "basename": "notes.txt"}])

    def test_symlinked_data_file_is_not_swept(self):
        # a data file in symlink mode must not contribute sweep dimensions
        data = _json_obj_path(self.tmp, "sweep.json", {"x": 1, "arr": [1, 2, 3]})
        fields = self._fields([{"path": data, "mode": "symlink"}])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1, "symlinked data file must not be swept")
        self.assertNotIn("sweep.json", variants[0]["files"])

    def test_default_file_mode_is_copy(self):
        txt = os.path.join(self.tmp, "notes.txt")
        with open(txt, "w") as f:
            f.write("hi\n")
        fields = self._fields([txt])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        v = variants[0]
        self.assertIn("notes.txt", v["files"])
        self.assertEqual(v["file_links"], [])

    def test_missing_symlinked_file_adds_warning(self):
        fields = self._fields([{"path": os.path.join(self.tmp, "nope.txt"),
                                "mode": "symlink"}])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(variants[0]["file_links"], [])
        self.assertTrue(any("nope.txt" in w for w in warnings), warnings)

    def test_build_jobs_does_not_symlink_to_disk(self):
        # builder is pure: symlinked files are described, never created.
        txt = os.path.join(self.tmp, "notes.txt")
        with open(txt, "w") as f:
            f.write("hi\n")
        root = os.path.join(self.tmp, "jobs")
        fields = self._fields([{"path": txt, "mode": "symlink"}])
        fields["job_root"] = root
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertFalse(os.path.exists(variants[0]["job_path"]))

    def test_same_basename_files_add_warning(self):
        os.makedirs(os.path.join(self.tmp, "a"))
        os.makedirs(os.path.join(self.tmp, "b"))
        pa = os.path.join(self.tmp, "a", "cfg.json")
        pb = os.path.join(self.tmp, "b", "cfg.json")
        with open(pa, "w") as f:
            f.write('{"a": 1}')
        with open(pb, "w") as f:
            f.write('{"b": 2}')
        fields = self._fields([pa, pb])
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertTrue(any("cfg.json" in w for w in warnings), warnings)

    def test_empty_sweep_array_warns_and_keeps_base_job(self):
        data = os.path.join(self.tmp, "sweep.json")
        with open(data, "w") as f:
            f.write('{"lr": 0}\n')
        fields = self._fields([data])
        from diffing import compute_patch
        fields["file_edits"] = {data: compute_patch('{"lr": 0}\n', '{"lr": []}\n')}
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertTrue(any("empty sweep" in w for w in warnings), warnings)

    def test_build_jobs_does_not_write_directories_to_disk(self):
        # builder is pure: it only describes dirs, never creates job dirs.
        src = os.path.join(self.tmp, "assets")
        os.makedirs(src)
        with open(os.path.join(src, "f.txt"), "w") as f:
            f.write("hi")
        root = os.path.join(self.tmp, "jobs")
        fields = self._fields([])
        fields["job_root"] = root
        fields["included_dirs"] = [{"path": src}]
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertFalse(os.path.exists(variants[0]["job_path"]))

    def test_build_jobs_missing_included_files_key_is_safe(self):
        fields = self._fields([])
        fields.pop("included_files")
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)

    def test_cartesian_sweep_across_two_json_files(self):
        # On disk the JSON files have scalar defaults; the edited copies carry
        # arrays, which is how a sweep is expressed.
        f1 = _json_obj_path(self.tmp, "a.json", {"x": 1, "s": 10})
        f2 = _json_obj_path(self.tmp, "b.json", {"y": "a", "t": "p"})
        fields = self._fields([
            {"path": f1},
            {"path": f2},
        ], edits={
            f1: json.dumps({"x": 1, "s": [10, 20, 30]}),
            f2: json.dumps({"y": "a", "t": ["p", "q"]}),
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 6)
        swept = {json.loads(v["files"]["a.json"])["s"] for v in variants}
        self.assertEqual(swept, {10, 20, 30})
        swept2 = {json.loads(v["files"]["b.json"])["t"] for v in variants}
        self.assertEqual(swept2, {"p", "q"})
        self.assertEqual({json.loads(v["files"]["a.json"])["x"] for v in variants}, {1})

    def test_job_body_appended_not_copied(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("payload\n")
        fields = self._fields([{"path": data}], job_body="#SBATCH --qos=fast\necho hello\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        v = variants[0]
        self.assertIn("data.txt", v["files"])
        self.assertIn("echo hello", v["job"])
        self.assertIn("#SBATCH --qos=fast", v["job"])

    def test_no_included_files_produces_single_base_job(self):
        fields = self._fields([], job_body="echo x\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        v = variants[0]
        self.assertEqual(v["files"], {})
        self.assertIn("#!/bin/sh", v["job"])
        self.assertIn("echo x", v["job"])

    def test_job_body_not_listed_in_files(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("payload\n")
        fields = self._fields([{"path": data}], job_body="echo body\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(list(variants[0]["files"].keys()), ["data.txt"])

    def test_file_edit_applied_from_patch(self):
        import diffing
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("one\ntwo\nthree\n")
        patch = diffing.compute_patch("one\ntwo\nthree\n", "one\nTWO\nthree\n")
        self.assertTrue(diffing.is_patch(patch))
        fields = self._fields([{"path": data}], edits={data: patch})
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(variants[0]["files"]["data.txt"], "one\nTWO\nthree\n")

    def test_file_edit_legacy_full_text(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("one\ntwo\n")
        fields = self._fields([{"path": data}], edits={data: "full override\n"})
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(variants[0]["files"]["data.txt"], "full override\n")

    def test_source_files_untouched(self):
        f1 = _json_obj_path(self.tmp, "a.json", {"x": 1, "s": [1, 2]})
        original = open(f1).read()
        fields = self._fields([{"path": f1}])
        builder.build_jobs(fields)
        self.assertEqual(open(f1).read(), original)

    def test_job_body_ordered_between_header_and_content(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("payload\n")
        fields = self._fields([{"path": data}], job_body="#SBATCH --qos=fast\necho body\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        job = variants[0]["job"]
        header_marker = "#SBATCH" if "#SBATCH" in job else "#PBS"
        self.assertLess(job.index(header_marker), job.index("#SBATCH --qos=fast"))
        self.assertLess(job.index("#SBATCH --qos=fast"), job.index("echo body"))

    def test_edit_applies_to_copy_only(self):
        spath = os.path.join(self.tmp, "data.txt")
        with open(spath, "w") as f:
            f.write("original\n")
        fields = self._fields([{"path": spath}], edits={spath: "edited copy\n"})
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(variants[0]["files"]["data.txt"], "edited copy\n")
        self.assertEqual(open(spath).read(), "original\n")

    def test_email_in_slurm_header(self):
        old = config.use_slurm
        config.use_slurm = True
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#SBATCH --mail-user=alice@example.com", variants[0]["job"])
            self.assertIn("job.slurm", variants[0]["job_file"])
        finally:
            config.use_slurm = old

    def test_slurm_mail_type_custom_values_joined(self):
        old = config.use_slurm
        config.use_slurm = True
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            fields["mail_type"] = ["BEGIN", "FAIL"]
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#SBATCH --mail-type=BEGIN,FAIL", variants[0]["job"])
            self.assertIn("#SBATCH --mail-user=alice@example.com", variants[0]["job"])
        finally:
            config.use_slurm = old

    def test_slurm_mail_type_empty_renders_none(self):
        old = config.use_slurm
        config.use_slurm = True
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            fields["mail_type"] = []
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#SBATCH --mail-type=NONE", variants[0]["job"])
        finally:
            config.use_slurm = old

    def test_slurm_mail_type_missing_defaults_to_all(self):
        old = config.use_slurm
        config.use_slurm = True
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#SBATCH --mail-type=ALL", variants[0]["job"])
        finally:
            config.use_slurm = old

    def test_email_in_pbs_header(self):
        old = config.use_slurm
        config.use_slurm = False
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#PBS -M alice@example.com", variants[0]["job"])
            self.assertIn("job.pbs", variants[0]["job_file"])
        finally:
            config.use_slurm = old

    def test_empty_email_omits_mail_lines(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}], job_body="echo x\n")
        fields["email"] = ""
        old = config.use_slurm
        try:
            config.use_slurm = True
            variants, _, _ = builder.build_jobs(fields)
            self.assertNotIn("#SBATCH --mail-type", variants[0]["job"])
            self.assertNotIn("#SBATCH --mail-user", variants[0]["job"])
            config.use_slurm = False
            variants, _, _ = builder.build_jobs(fields)
            self.assertNotIn("#PBS -m", variants[0]["job"])
            self.assertNotIn("#PBS -M", variants[0]["job"])
        finally:
            config.use_slurm = old

    def test_job_name_uses_job_root_basename_slurm(self):
        old = config.use_slurm
        config.use_slurm = True
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#SBATCH --job-name=jobs-1", variants[0]["job"])
        finally:
            config.use_slurm = old

    def test_job_name_uses_job_root_basename_pbs(self):
        old = config.use_slurm
        config.use_slurm = False
        try:
            data = os.path.join(self.tmp, "data.txt")
            with open(data, "w") as f:
                f.write("d\n")
            fields = self._fields([{"path": data}], job_body="echo x\n")
            variants, _, _ = builder.build_jobs(fields)
            self.assertIn("#PBS -N jobs-1", variants[0]["job"])
        finally:
            config.use_slurm = old

    def test_single_job_mode_writes_directly_in_root(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}],
                              job_body="echo job={{JOBID}} dir={{JOBDIR}}\n")
        fields["single_job"] = True
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        root = os.path.abspath(fields["job_root"])
        self.assertEqual(variants[0]["job_path"], root)
        job = variants[0]["job"]
        self.assertIn("--job-name=jobs", job)
        self.assertNotIn("--job-name=jobs-", job)
        self.assertIn("echo job=jobs dir=" + root, job)
        self.assertNotIn("{{JOBID}}", job)
        self.assertNotIn("{{JOBDIR}}", job)

    def test_single_job_mode_uses_settable_job_name(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}],
                              job_body="echo job={{JOBID}}\n")
        fields["single_job"] = True
        fields["job_name"] = "myjob"
        variants, _, _ = builder.build_jobs(fields)
        self.assertIn("#SBATCH --job-name=myjob", variants[0]["job"])
        self.assertIn("echo job=myjob", variants[0]["job"])

    def test_single_job_mode_ignores_sweeps_with_warning(self):
        f1 = _json_obj_path(self.tmp, "a.json", {"x": 1})
        fields = self._fields([{"path": f1}], job_body="echo x\n",
                              edits={f1: json.dumps({"x": [1, 2]})})
        fields["single_job"] = True
        variants, _, warnings = builder.build_jobs(fields)
        self.assertEqual(len(variants), 1)
        self.assertTrue(any("ignoring 1 sweep dimension" in w for w in warnings),
                        warnings)

    def test_job_name_prefix_in_multi_mode(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}],
                              job_body="echo job={{JOBID}}\n")
        fields["job_name"] = "myjob"
        variants, _, _ = builder.build_jobs(fields)
        self.assertIn("#SBATCH --job-name=myjob-1", variants[0]["job"])
        self.assertIn("echo job=1", variants[0]["job"])

    def test_existing_targets_empty_when_nothing_present(self):
        os.makedirs(os.path.join(self.tmp, "jobs"))
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}])
        variants, _, _ = builder.build_jobs(fields)
        self.assertEqual(builder.existing_targets(variants), [])

    def test_existing_targets_flags_existing_job_file(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}])
        variants, _, _ = builder.build_jobs(fields)
        os.makedirs(variants[0]["job_path"], exist_ok=True)
        with open(variants[0]["job_file"], "w") as f:
            f.write("x\n")
        conflicts = builder.existing_targets(variants)
        self.assertTrue(any(c["kind"] == "job file" for c in conflicts), conflicts)

    def test_existing_targets_flags_existing_copy_file(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}])
        variants, _, _ = builder.build_jobs(fields)
        os.makedirs(variants[0]["job_path"], exist_ok=True)
        target = os.path.join(variants[0]["job_path"], "data.txt")
        with open(target, "w") as f:
            f.write("old\n")
        conflicts = builder.existing_targets(variants)
        self.assertTrue(any(c["kind"] == "file" and c["path"] == target
                            for c in conflicts), conflicts)

    def test_existing_targets_flags_dangling_symlink(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}])
        variants, _, _ = builder.build_jobs(fields)
        os.makedirs(variants[0]["job_path"], exist_ok=True)
        target = os.path.join(variants[0]["job_path"], "data.txt")
        os.symlink("/nonexistent/target", target)
        conflicts = builder.existing_targets(variants)
        self.assertTrue(any(c["kind"] == "file" and c["path"] == target
                            for c in conflicts), conflicts)

    def test_existing_targets_flags_job_dir_that_is_a_file(self):
        os.makedirs(os.path.join(self.tmp, "jobs"))
        with open(os.path.join(self.tmp, "jobs", "1"), "w") as f:
            f.write("not a dir\n")
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}])
        variants, _, _ = builder.build_jobs(fields)
        conflicts = builder.existing_targets(variants)
        self.assertTrue(any(c["kind"] == "job dir is a file" for c in conflicts),
                        conflicts)

    def test_existing_targets_single_mode_ignores_root_dir_but_flags_job_file(self):
        root = os.path.join(self.tmp, "jobs")
        os.makedirs(root)
        with open(os.path.join(root, "unrelated.txt"), "w") as f:
            f.write("keep\n")
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}])
        fields["single_job"] = True
        variants, _, _ = builder.build_jobs(fields)
        self.assertEqual(variants[0]["job_path"], os.path.abspath(root))
        self.assertEqual(builder.existing_targets(variants), [])
        with open(variants[0]["job_file"], "w") as f:
            f.write("x\n")
        conflicts = builder.existing_targets(variants)
        self.assertTrue(any(c["kind"] == "job file" for c in conflicts), conflicts)

    def test_placeholder_substitution_in_job_body(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}], job_body="echo job={{JOBID}} dir={{JOBDIR}}\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        job = variants[0]["job"]
        job_dir = os.path.join(os.path.join(self.tmp, "jobs"), "1")
        self.assertIn("echo job=1 dir=" + job_dir, job)
        self.assertNotIn("{{JOBID}}", job)

    def test_placeholder_substitution_in_job_body_directives(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}], job_body="# job dir {{JOBDIR}} id {{JOBID}}\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        job_dir = os.path.join(os.path.join(self.tmp, "jobs"), "1")
        self.assertIn("# job dir " + job_dir + " id 1", variants[0]["job"])

    def test_placeholder_substitution_per_variant(self):
        f = _json_obj_path(self.tmp, "a.json", {"x": 1})
        fields = self._fields([{"path": f}], edits={f: json.dumps({"x": [10, 20]})},
                              job_body="echo id={{JOBID}} dir={{JOBDIR}}\n")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 2)
        base = os.path.join(self.tmp, "jobs")
        self.assertIn("echo id=1 dir=" + os.path.join(base, "1"), variants[0]["job"])
        self.assertIn("echo id=2 dir=" + os.path.join(base, "2"), variants[1]["job"])

    def test_unknown_placeholder_left_untouched(self):
        self.assertEqual(
            builder.substitute_placeholders("{{NOPE}} {{JOBID}}", {"JOBID": "7"}),
            "{{NOPE}} 7",
        )

    def test_unknown_tokens_helper(self):
        self.assertEqual(builder.unknown_tokens("{{JOBID}} {{JOBDIR}} ok"), [])
        self.assertEqual(
            builder.unknown_tokens("{{JODDIR}} {{NOPE}} {{JOBID}}"),
            ["JODDIR", "NOPE"],
        )

    def test_unknown_tokens_catches_mixed_case_typos(self):
        self.assertEqual(
            builder.unknown_tokens("{{JOBDIssR}} {{jobdir}} {{JOBID}}"),
            ["JOBDIssR", "jobdir"],
        )

    def test_build_jobs_warns_on_unknown_token(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}], job_body="cd {{JODDIR}}\n")
        variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)
        self.assertIn("{{JODDIR}}", variants[0]["job"])
        self.assertTrue(any("JODDIR" in w for w in warnings))

    def test_build_jobs_no_warning_on_known_tokens(self):
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as f:
            f.write("d\n")
        fields = self._fields([{"path": data}], job_body="echo {{JOBID}} {{JOBDIR}}\n")
        _variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(warnings, [])

    def _yaml_path(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_yaml_sweep(self):
        p = self._yaml_path("c.yaml", "lr: 0.1\nbatch: 32\nname: exp\n")
        fields = self._fields([{"path": p}], edits={
            p: "lr: [0.1, 0.2, 0.3]\nbatch: 32\nname: exp\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 3)
        lrs = []
        for v in variants:
            content = v["files"]["c.yaml"]
            self.assertIn("batch: 32", content)
            lrs.append(content.split("lr:")[1].splitlines()[0].strip())
        self.assertEqual(sorted(lrs), ["0.1", "0.2", "0.3"])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_yaml_and_json_combine_cartesian(self):
        fj = _json_obj_path(self.tmp, "a.json", {"x": 1})
        fy = self._yaml_path("b.yaml", "y: p\n")
        fields = self._fields([{"path": fj}, {"path": fy}], edits={
            fj: json.dumps({"x": [1, 2]}),
            fy: "y: [p, q]\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 4)

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_yaml_non_swept_scalar_preserved(self):
        p = self._yaml_path("c.yaml", "lr: 0.1\nbatch: 32\n")
        fields = self._fields([{"path": p}], edits={
            p: "lr: 0.1\nbatch: [16, 32]\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 2)
        for v in variants:
            self.assertIn("lr: 0.1", v["files"]["c.yaml"])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_yaml_arrays_of_maps_not_swept(self):
        p = self._yaml_path("c.yaml", "layers: []\n")
        fields = self._fields([{"path": p}], edits={
            p: "layers:\n  - {a: 1}\n  - {a: 2}\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)

    def test_validate_data_json_and_yaml(self):
        self.assertIsNone(builder.validate_data("a.json", '{"x": 1}'))
        self.assertIsNotNone(builder.validate_data("a.json", "{oops"))
        self.assertIsNone(builder.validate_data("a.txt", "{oops"))
        if builder.HAS_YAML:
            self.assertIsNone(builder.validate_data("a.yaml", "x: 1"))
            self.assertIsNotNone(builder.validate_data("a.yaml", "x: [1,"))

    def test_sweep_value_spans_json_flow(self):
        spans = builder.sweep_value_spans("a.json", '{\n  "lr": [0.1, 0.2]\n}\n')
        self.assertEqual(spans, [(1, 8, 18)])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_sweep_value_spans_yaml_flow(self):
        spans = builder.sweep_value_spans("a.yaml", "lr: [0.1, 0.2]\nbatch: 32\n")
        self.assertEqual(spans, [(0, 4, 14)])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_sweep_value_spans_yaml_block(self):
        spans = builder.sweep_value_spans("a.yaml", "lr:\n  - 0.1\n  - 0.2\nbatch: 32\n")
        self.assertEqual(spans, [(1, 2, 7), (2, 2, 7)])

    def test_sweep_value_spans_none_when_not_swept(self):
        self.assertEqual(builder.sweep_value_spans("a.json", '{"x": 1}\n'), [])
        self.assertEqual(builder.sweep_value_spans("a.yaml", "x: 1\n"), [])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_nested_yaml_sweep(self):
        p = self._yaml_path("c.yaml",
                            "data:\n  lr: 0.1\n  name: x\nbatch: 32\n")
        fields = self._fields([{"path": p}], edits={
            p: "data:\n  lr: [0.1, 0.2, 0.3]\n  name: x\nbatch: 32\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 3)
        lrs = []
        for v in variants:
            lrs.append(v["label"])
        self.assertIn("c.yaml.data.lr:0.1", lrs)
        self.assertIn("c.yaml.data.lr:0.2", lrs)
        self.assertIn("c.yaml.data.lr:0.3", lrs)
        for v in variants:
            self.assertIn("name: x", v["files"]["c.yaml"])

    def test_string_sweep_json(self):
        fj = _json_obj_path(self.tmp, "a.json", {"model": "resnet"})
        fields = self._fields([{"path": fj}], edits={
            fj: json.dumps({"model": ["resnet", "densenet", "unet"]}),
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 3)
        labels = sorted(v["label"] for v in variants)
        self.assertEqual(labels, ["a.json.model:densenet",
                                  "a.json.model:resnet",
                                  "a.json.model:unet"])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_string_sweep_yaml(self):
        p = self._yaml_path("c.yaml", "model: resnet\nlr: 0.1\n")
        fields = self._fields([{"path": p}], edits={
            p: "model: [resnet, densenet, unet]\nlr: 0.1\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 3)
        models = []
        for v in variants:
            content = v["files"]["c.yaml"]
            self.assertIn("lr: 0.1", content)
            models.append(content.split("model:")[1].splitlines()[0].strip())
        self.assertEqual(sorted(models), ["densenet", "resnet", "unet"])

    def test_nested_json_sweep(self):
        fj = _json_obj_path(self.tmp, "a.json", {"data": {"lr": 0.1}, "batch": 32})
        fields = self._fields([{"path": fj}], edits={
            fj: json.dumps({"data": {"lr": [0.1, 0.2]}, "batch": 32}),
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 2)
        labels = sorted(v["label"] for v in variants)
        self.assertEqual(labels, ["a.json.data.lr:0.1", "a.json.data.lr:0.2"])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_nested_and_top_level_combine_cartesian(self):
        fj = _json_obj_path(self.tmp, "a.json", {"CodeLength": 256})
        fy = self._yaml_path("b.yaml", "data:\n  lr: 0.1\n")
        fields = self._fields([{"path": fj}, {"path": fy}], edits={
            fj: json.dumps({"CodeLength": [256, 128]}),
            fy: "data:\n  lr: [0.1, 0.3, 0.5]\n",
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 6)

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_nested_yaml_block_highlight(self):
        spans = builder.sweep_value_spans(
            "b.yaml", "data:\n  lr:\n    - 0.1\n    - 0.2\n  name: x\n")
        self.assertEqual(spans, [(2, 4, 9), (3, 4, 9)])

    @unittest.skipUnless(builder.HAS_YAML, "PyYAML not installed")
    def test_nested_yaml_flow_highlight(self):
        spans = builder.sweep_value_spans(
            "b.yaml", "data:\n  lr: [0.1, 0.2]\n  name: x\n")
        self.assertEqual(spans, [(1, 6, 16)])

    def test_nested_arrays_of_maps_not_swept(self):
        fj = _json_obj_path(self.tmp, "a.json", {"layers": []})
        fields = self._fields([{"path": fj}], edits={
            fj: json.dumps({"layers": [{"a": 1}, {"a": 2}]}),
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 1)

    def test_nested_source_arrays_not_swept(self):
        fj = _json_obj_path(self.tmp, "a.json",
                            {"data": {"dims": [512, 512], "lr": 0.1}})
        fields = self._fields([{"path": fj}], edits={
            fj: json.dumps({"data": {"dims": [512, 512], "lr": [0.1, 0.2]}}),
        })
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 2)
        for v in variants:
            obj = json.loads(v["files"]["a.json"])
            self.assertEqual(obj["data"]["dims"], [512, 512])

    def test_multiline_flow_span(self):
        spans = builder.sweep_value_spans(
            "a.json", '{\n  "lr": [\n    0.1,\n    0.2\n  ]\n}\n')
        self.assertEqual(spans, [(1, 8, 9), (2, 0, 8), (3, 0, 7), (4, 0, 3)])


class JobChainsTest(unittest.TestCase):
    def test_no_chain_when_single_job(self):
        self.assertEqual(builder.job_chains(1, 5), [])

    def test_no_chain_when_unlimited(self):
        self.assertEqual(builder.job_chains(5, 0), [])

    def test_series_single_chain(self):
        self.assertEqual(builder.job_chains(5, 1), [[0, 1, 2, 3, 4]])

    def test_two_round_robin_chains(self):
        self.assertEqual(builder.job_chains(5, 2), [[0, 2, 4], [1, 3]])

    def test_parallelism_gt_jobs(self):
        self.assertEqual(builder.job_chains(3, 5), [[0], [1], [2]])


class DependencyFlagsTest(unittest.TestCase):
    def test_slurm_with_dep(self):
        self.assertEqual(builder.dependency_flags("slurm", "42"),
                         ["--dependency=afterok:42"])

    def test_pbs_with_dep(self):
        self.assertEqual(builder.dependency_flags("pbs", "42.host"),
                         ["-W", "depend=afterok:42.host"])

    def test_no_dep(self):
        self.assertEqual(builder.dependency_flags("slurm", None), [])
        self.assertEqual(builder.dependency_flags("pbs", ""), [])


class ParseImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    SLURM = """#!/bin/sh
#SBATCH --job-name=my-job-34
#SBATCH --mail-type=ALL
#SBATCH --mail-user=user@example.com
#SBATCH --qos=qos_gpu-t3
#SBATCH --gres=gpu:1
#SBATCH --time=20:00:00
#SBATCH --output=/tmp/log.out
#SBATCH --error=/tmp/log.out
module purge
module load git
cd /tmp
python train.py
"""

    PBS = """#!/bin/sh
#PBS -l walltime=5000:00:00
#PBS -N my-job-161
#PBS -q gpu
#PBS -l nodes=1:ppn=10:gpus=1
#PBS -o /tmp/log.out
#PBS -e /tmp/log.err
#PBS -m ae
#PBS -M user@example.com
hostname
conda activate myenv
python train.py
"""

    def test_slurm_email_extracted(self):
        email, _, _ = builder.parse_import(self.SLURM, True)
        self.assertEqual(email, "user@example.com")

    def test_slurm_mail_type_extracted(self):
        _, mail_types, _ = builder.parse_import(self.SLURM, True)
        self.assertEqual(mail_types, ["ALL"])

    def test_slurm_mail_directives_space_separated(self):
        # Regression: `--mail-user a@b.c` (no `=`) must still be captured and
        # the directive line stripped, not dropped silently.
        src = ("#!/bin/sh\n"
               "#SBATCH --job-name=test\n"
               "#SBATCH --mail-user user@example.com\n"
               "#SBATCH --mail-type BEGIN,FAIL\n"
               "python train.py\n")
        email, mail_types, body = builder.parse_import(src, True)
        self.assertEqual(email, "user@example.com")
        self.assertEqual(mail_types, ["BEGIN", "FAIL"])
        self.assertNotIn("--mail-user", body)
        self.assertNotIn("--mail-type", body)
        self.assertIn("python train.py", body)

    def test_pbs_email_extracted(self):
        email, _, _ = builder.parse_import(self.PBS, False)
        self.assertEqual(email, "user@example.com")

    def test_slurm_header_fields_removed_from_body(self):
        _, _, body = builder.parse_import(self.SLURM, True)
        self.assertIn("#SBATCH --qos=qos_gpu-t3", body)
        self.assertIn("#SBATCH --gres=gpu:1", body)
        self.assertIn("#SBATCH --time=20:00:00", body)
        self.assertNotIn("--job-name", body)
        self.assertNotIn("--mail-type", body)
        self.assertNotIn("--mail-user", body)
        self.assertNotIn("--output", body)
        self.assertNotIn("--error", body)

    def test_pbs_header_fields_removed_from_body(self):
        _, _, body = builder.parse_import(self.PBS, False)
        self.assertIn("#PBS -l walltime=5000:00:00", body)
        self.assertIn("#PBS -q gpu", body)
        self.assertIn("#PBS -l nodes=1:ppn=10:gpus=1", body)
        self.assertNotIn("-N ", body)
        self.assertNotIn("-m ae", body)
        self.assertNotIn("-M ", body)
        self.assertNotIn("-o ", body)
        self.assertNotIn("-e ", body)

    def test_body_merges_directives_and_commands_in_order(self):
        _, _, body = builder.parse_import(self.SLURM, True)
        self.assertNotIn("#!", body)
        self.assertLess(body.index("#SBATCH --qos=qos_gpu-t3"),
                        body.index("module purge"))
        self.assertLess(body.index("module purge"), body.index("python train.py"))

    def test_pbs_body_merged_shebang_removed(self):
        _, _, body = builder.parse_import(self.PBS, False)
        self.assertNotIn("#!", body)
        self.assertLess(body.index("#PBS -q gpu"), body.index("hostname"))
        self.assertLess(body.index("hostname"), body.index("python train.py"))

    def test_roundtrip_through_build_jobs(self):
        email, mail_types, body = builder.parse_import(self.SLURM, True)
        fields = {
            "job_root": os.path.join(self.tmp, "jobs"),
            "email": email,
            "mail_type": mail_types,
            "job_body": body,
            "included_files": [],
            "file_edits": {},
        }
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        job = variants[0]["job"]
        self.assertIn("#SBATCH --mail-user=" + email, job)
        self.assertIn("#SBATCH --qos=qos_gpu-t3", job)
        self.assertNotIn("--job-name=my-job-34", job)
        self.assertIn("python train.py", job)


class BodySweepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _fields(self, job_body, included=None, edits=None):
        return {
            "job_root": os.path.join(self.tmp, "jobs"),
            "email": "alice@example.com",
            "job_body": job_body,
            "included_files": included or [],
            "file_edits": edits or {},
        }

    def test_collect_body_sweeps_scalar_types(self):
        body = ('python train.py --lr [[1,2,3]] --name [["one","two"]] '
                '--flag [[true,false]]')
        sweeps = builder.collect_body_sweeps(body)
        self.assertEqual(sweeps, {
            "[[1,2,3]]": [1, 2, 3],
            '[["one","two"]]': ["one", "two"],
            "[[true,false]]": [True, False],
        })

    def test_collect_body_sweeps_single_element_and_dedup(self):
        body = "echo [[5]] && echo [[5]]"
        sweeps = builder.collect_body_sweeps(body)
        self.assertEqual(sweeps, {"[[5]]": [5]})

    def test_collect_body_sweeps_ignores_single_brackets(self):
        sweeps = builder.collect_body_sweeps('echo ${arr[0]} [ -f x ] [[1,2]]')
        self.assertEqual(sweeps, {"[[1,2]]": [1, 2]})

    def test_collect_body_sweeps_quote_aware(self):
        sweeps = builder.collect_body_sweeps('--x [["a]b"]]')
        self.assertEqual(sweeps, {'[["a]b"]]': ["a]b"]})

    def test_body_sweep_ranges_valid_and_invalid(self):
        ranges = builder.body_sweep_ranges("--lr [[1,2,3]]")
        self.assertEqual(ranges, [(0, 5, 14, True)])
        ranges = builder.body_sweep_ranges("--x [[1,2,")
        self.assertTrue(ranges and all(not ok for *_ok, ok in ranges))
        ranges = builder.body_sweep_ranges("--x [[a,b]]")
        self.assertTrue(ranges and all(not ok for *_ok, ok in ranges))
        ranges = builder.body_sweep_ranges("--x [[5]]")
        self.assertEqual(ranges, [(0, 4, 9, True)])

    def test_body_sweep_errors(self):
        self.assertEqual(builder.body_sweep_errors("--lr [[1,2,"),
                         ["malformed script sweep: [[1,2,"])
        self.assertEqual(builder.body_sweep_errors("--x [[a,b]]"),
                         ["malformed script sweep: [[a,b]]"])
        self.assertEqual(builder.body_sweep_errors("echo [[1,2"),
                         ["malformed script sweep: [[1,2"])
        self.assertEqual(builder.body_sweep_errors("--lr [[1,2,3]] [[5]]"), [])
        self.assertEqual(builder.body_sweep_errors("echo [1,2] ${arr[0]}"), [])

    def test_body_sweep_errors_truncates_long_snippet(self):
        errs = builder.body_sweep_errors("x [[%s]]" % ("a" * 80))
        self.assertEqual(len(errs), 1)
        self.assertLessEqual(len(errs[0]), 40 + len("malformed script sweep: ") + 3)

    def test_build_jobs_warns_on_malformed_body_sweep(self):
        fields = self._fields("echo [[1,2,")
        _variants, err, warnings = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertTrue(any("malformed script sweep" in w for w in warnings))

    def test_build_jobs_no_warning_on_valid_body_sweep(self):
        fields = self._fields("echo [[1,2,3]]")
        _variants, _err, warnings = builder.build_jobs(fields)
        self.assertEqual([w for w in warnings if "sweep" in w], [])

    def test_build_jobs_cartesian_body_sweeps(self):
        fields = self._fields("python train.py --lr [[1,2,3]] --n [[\"a\",\"b\"]]")
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 6)
        combos = set()
        for v in variants:
            line = [l for l in v["job"].split("\n") if "python" in l][0]
            combos.add(line)
            self.assertNotIn("[[", line)
        self.assertEqual(combos, {
            "python train.py --lr 1 --n a",
            "python train.py --lr 1 --n b",
            "python train.py --lr 2 --n a",
            "python train.py --lr 2 --n b",
            "python train.py --lr 3 --n a",
            "python train.py --lr 3 --n b",
        })

    def test_build_jobs_body_sweep_labels(self):
        fields = self._fields("echo [[1,2]]")
        variants, _, _ = builder.build_jobs(fields)
        self.assertEqual({v["label"] for v in variants}, {"[[1,2]]:1", "[[1,2]]:2"})

    def test_build_jobs_combines_with_data_file_sweeps(self):
        data = _json_obj_path(self.tmp, "cfg.json", {"lr": 0.5})
        fields = self._fields("python t.py --lr [[0.1,0.2]]",
                              included=[{"path": data}],
                              edits={data: json.dumps({"lr": [0.5, 0.7]})})
        variants, err, _ = builder.build_jobs(fields)
        self.assertIsNone(err)
        self.assertEqual(len(variants), 4)
        self.assertEqual({json.loads(v["files"]["cfg.json"])["lr"]
                          for v in variants}, {0.5, 0.7})
        lines = {l for v in variants
                 for l in v["job"].split("\n") if "python" in l}
        self.assertEqual(lines, {"python t.py --lr 0.1", "python t.py --lr 0.2"})

    def test_build_jobs_no_sweep_when_no_double_brackets(self):
        fields = self._fields("echo [1,2] plain")
        variants, _, _ = builder.build_jobs(fields)
        self.assertEqual(len(variants), 1)
        self.assertIn("echo [1,2] plain", variants[0]["job"])


if __name__ == "__main__":
    unittest.main()
