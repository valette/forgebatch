"""runner.py - submission and scheduler-output helpers.

Kept Textual-free (stdlib only, plus the pure builder module) so the most
complex non-UI logic in the app can be unit-tested in isolation.
"""

import subprocess
import xml.etree.ElementTree as ET

from builder import dependency_flags, job_chains


def run_submission(variants, root, parallelism, scheduler, use_slurm, run=None):
    """Submit `variants` under the job root, honouring the parallelism limit.

    Returns (log, submitted, failed). Dependencies are injected at submission
    time (job ids only exist after submit): each chain is submitted in order,
    the returned id is captured (sbatch --parsable / qsub stdout) and passed to
    the next job in the chain.

    `run` defaults to subprocess.run and is injectable for tests.
    """
    if run is None:
        run = subprocess.run
    exe = "sbatch" if use_slurm else "qsub"
    submitted = 0
    failed = 0
    log = []

    def submit_one(v, dep_id):
        nonlocal submitted, failed
        cmd = [exe]
        cmd += dependency_flags(scheduler, dep_id)
        if use_slurm:
            cmd.append("--parsable")
        cmd.append(v["job_file"])
        entry = {"label": v.get("label", ""), "job_file": v["job_file"],
                 "cmd": list(cmd), "returncode": None, "stdout": "",
                 "stderr": "", "error": "", "ok": False}
        try:
            proc = run(cmd, cwd=root, capture_output=True, text=True)
        except OSError as e:
            entry["error"] = str(e)
            log.append(entry)
            failed += 1
            return None
        entry["returncode"] = proc.returncode
        entry["stdout"] = proc.stdout or ""
        entry["stderr"] = proc.stderr or ""
        if proc.returncode != 0:
            log.append(entry)
            failed += 1
            return None
        entry["ok"] = True
        submitted += 1
        log.append(entry)
        return proc.stdout.strip()

    chains = job_chains(len(variants), parallelism)
    if not chains:
        for v in variants:
            submit_one(v, None)
    else:
        for chain in chains:
            dep = None
            for idx in chain:
                dep = submit_one(variants[idx], dep)
    return log, submitted, failed


def parse_duration(text):
    """Parse an HH:MM:SS or DD-HH:MM:SS duration into whole seconds.

    Returns None for empty/absent/`UNLIMITED`/`--` values (no parseable limit).
    """
    if not text:
        return None
    t = text.strip()
    if t in ("UNLIMITED", "N/A", "--", "-", "0"):
        return None
    days = 0
    if "-" in t:
        dpart, t = t.split("-", 1)
        if dpart.isdigit():
            days = int(dpart)
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, m, s = 0, parts[0], parts[1]
        else:
            return None
        return days * 86400 + int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return None


def state_category(state):
    """Map a scheduler state string to a coarse category.

    SLURM passes the `%T` state word (RUNNING, PENDING, COMPLETED, FAILED, ...);
    PBS passes its single-letter status (R/Q/H/E/C/S). Unknown strings map to
    "unknown". The category drives the row's color in the view."""
    s = (state or "").strip().upper()
    if not s:
        return "unknown"
    if s in ("RUNNING", "R", "COMPLETING", "CG"):
        return "running"
    if s in ("PENDING", "PD", "SUSPENDED", "S", "CONFIGURING", "CF",
             "REQUEUED", "REQUEUE_HOLD", "Q", "H", "ST", "STOPPED"):
        return "pending"
    if s in ("COMPLETED", "C", "DONE", "F"):
        return "completed"
    if s in ("FAILED", "F", "DEADLINE", "DL", "OUT_OF_MEMORY", "OOM",
             "NODE_FAIL", "BOOT_FAIL", "PREEMPTED", "E", "TIMEOUT", "TO"):
        return "failed"
    if s in ("CANCELLED", "CA", "CANCELED"):
        return "cancelled"
    return "unknown"


def progress_cells(elapsed, limit, width=10):
    """Return (percent, bar_string) for elapsed/limit seconds.

    When limit is missing/None the bar is blank (a row of spaces) and percent
    is None, so the view can omit the progress column entirely."""
    if limit is None or elapsed is None or limit <= 0:
        return (None, " " * width)
    pct = max(0, min(100, int(round(elapsed / limit * 100))))
    filled = max(0, min(width, round(pct / 100 * width)))
    bar = "█" * filled + "░" * (width - filled)
    return (pct, bar)


def cancel_cmd(scheduler, job_id):
    """Return the CLI command to cancel a job (SLURM: scancel, PBS: qdel)."""
    return (["scancel", job_id] if scheduler == "slurm" else ["qdel", job_id])


def detail_cmd(use_slurm, job_id):
    """Return the CLI command that prints full details for a job."""
    return (["scontrol", "show", "job", job_id] if use_slurm
            else ["qstat", "-f", job_id])


def parse_running_output(text, use_slurm):
    """Parse squeue/qstat output into structured job records.

    Returns a list of dicts:
        {job_id, name, user, state, elapsed_sec, limit_sec, queue}
    The scheduler is queried for all jobs; the caller filters by user if
    desired. Slurm uses a `|`-separated
    `JOBID|USER|NAME|STATE|TIME|TIMELIMIT` format (`squeue --format=%i|%u|%j|%T|%M|%l`);
    PBS uses the XML output of `qstat -x` (see parse_qstat_xml), which preserves
    the full untruncated Job_Id and Job_Name even when the fixed-width `qstat`
    listing would cut them. Elapsed/limit are parsed into seconds."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    records = []
    if use_slurm:
        for ln in lines:
            parts = [p.strip() for p in ln.split("|")]
            if len(parts) >= 5 and parts[0] != "JOBID":
                limit = parse_duration(parts[5]) if len(parts) >= 6 else None
                records.append({
                    "job_id": parts[0], "name": parts[2], "user": parts[1],
                    "state": parts[3], "elapsed_sec": parse_duration(parts[4]),
                    "limit_sec": limit, "queue": ""})
    else:
        xml_records = parse_qstat_xml(text)
        if xml_records is not None:
            return xml_records
        for ln in lines:
            parts = ln.split()
            if not parts:
                continue
            if parts[0].startswith("Job") or all(all(c == "-" for c in tok) for tok in parts):
                continue
            if len(parts) >= 4:
                fields = (parts[:6] + [""] * 6)[:6]
                records.append({
                    "job_id": fields[0], "name": fields[1], "user": fields[2],
                    "state": fields[4], "elapsed_sec": parse_duration(fields[3]),
                    "limit_sec": None, "queue": fields[5]})
    return records


def parse_qstat_xml(text):
    """Parse `qstat -x` XML output into job records.

    PBS/Torque's fixed-column `qstat` listing truncates the Job id and Name
    columns, which breaks both the display and id-keyed actions (qstat -f /
    qdel). `qstat -x` emits each job's full attributes as XML, so the complete
    Job_Id and Job_Name are preserved here.

    Returns the same {job_id, name, user, state, elapsed_sec, limit_sec, queue}
    record dicts as parse_running_output, or None if the text is not XML (so
    callers can fall back to the fixed-column listing). elapsed_sec comes from
    `<resources_used><walltime>` (0 for jobs not yet started) and limit_sec from
    the requested `<Resource_List><walltime>` (None when unset)."""
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None
    records = []

    def child(job, tag):
        el = job.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    for job in root.iter("Job"):
        owner = child(job, "Job_Owner")
        user = owner.split("@", 1)[0] if owner else ""
        elapsed = ""
        for res in job.findall("resources_used"):
            wt = res.find("walltime")
            if wt is not None and wt.text:
                elapsed = wt.text.strip()
                break
        limit = ""
        for res in job.findall("Resource_List"):
            wt = res.find("walltime")
            if wt is not None and wt.text:
                limit = wt.text.strip()
                break
        records.append({
            "job_id": child(job, "Job_Id"), "name": child(job, "Job_Name"),
            "user": user, "state": child(job, "job_state"),
            "elapsed_sec": parse_duration(elapsed) if elapsed else 0,
            "limit_sec": parse_duration(limit), "queue": child(job, "queue")})
    return records
