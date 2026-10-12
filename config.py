"""config.py - constants and settings/preset persistence for forgebatch.

This module has no dependency on Textual or any other module in the project.
"""

import json
import os
import shutil

join = os.path.join

name = "forgebatch"


def detect_scheduler():
    """Return 'slurm', 'pbs', or None based on which scheduler binaries exist."""
    if shutil.which("sbatch") or shutil.which("srun"):
        return "slurm"
    if shutil.which("qsub") or shutil.which("qstat"):
        return "pbs"
    return None


scheduler = detect_scheduler()
use_slurm = scheduler == "slurm"
home_dir = os.environ.get("WORK") or os.environ["HOME"]
config_file = join(
    os.environ.get("XDG_CONFIG_HOME") or join(os.environ["HOME"], ".config"),
    "forgebatch", "config.json")


def set_config_file(path):
    """Override the global config file location (used by the --config CLI
    option and by tests to keep runs hermetic)."""
    global config_file
    config_file = path


def default_settings():
    """A fresh settings dict. Keys are used by the UI, the config store, and
    the presets. `email` is the full address used for scheduler notifications."""
    return {
        "job_root": join(home_dir, name),
        "job_name": "",
        "single_job": False,
        "email": "",
        "mail_type": ["ALL"],
        "parallelism_limit": "0",
        "job_body": "",
        "included_files": [],
        "included_dirs": [],
        "file_edits": {},
        "cluster_user": "",
    }


def read_config():
    """Load the on-disk config, normalising it to the
    shape {"last": <settings>, "selected_preset": str, "presets": {name: settings}}."""
    if not os.path.exists(config_file):
        return {"last": default_settings(), "selected_preset": "", "presets": {}}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (ValueError, OSError):
        return {"last": default_settings(), "selected_preset": "", "presets": {}}
    if "presets" not in cfg:
        cfg = {"last": cfg, "selected_preset": "", "presets": {}}
    last = dict(default_settings())
    last.update(cfg.get("last", {}))
    last.pop("config", None)
    cfg["last"] = last
    cfg.setdefault("selected_preset", "")
    cfg.setdefault("presets", {})
    return cfg


def write_config(cfg):
    """Persist the config to disk. Failures are ignored (non-fatal)."""
    try:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def next_job_id(job_root):
    """Return the next auto-incremented job id (max numeric dir name + 1).

    Does not create the directory (callers that write jobs create it), so a
    read-only preview never has the side effect of creating job_root and never
    crashes on a missing parent path."""
    if not os.path.isdir(job_root):
        return "1"
    max_job_id = 0
    for d in os.listdir(job_root):
        if not os.path.isdir(join(job_root, d)):
            continue
        try:
            max_job_id = max(max_job_id, int(d))
        except ValueError:
            continue
    return str(max_job_id + 1)
