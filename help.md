# Help — forgebatch

An interactive terminal application that generates **SLURM/PBS job
directories** for HPC scheduler experiments. Everything is driven by the
on-screen buttons; the keyboard shortcuts are **Escape** (close a dialog, or
quit the app on the main screen), **Enter** (confirm a dialog), **Ctrl+S**
(save immediately) and **Ctrl+X** (quit the app from anywhere). To paste text
(e.g. into the job body or the **Import job from text** dialog), use
**Shift+Ctrl+V** — the usual **Ctrl+V** does not work in the terminal.

## Typical workflow

A typical session goes like this:

1. **Set up the Settings tab.** Give a **Jobs root directory** (the base folder
   under which each generated job gets its own numbered subdirectory) and, if
   you want email notifications, your **email** address. The read-only
   **scheduler header** updates as you type so you can verify the `#SBATCH` /
   `#PBS` lines before anything is written.
 2. **Prepare the job body.** Use **Import job from file** (or **Import job
    from text**) to load an existing SLURM/PBS job: it populates the **email**
    field and the **job body** (the whole job after the header, with the
    header-owned fields removed). The body is placed right after the generated
    scheduler header. You can edit it inline directly. Use the
    `{{JOBID}}` / `{{JOBDIR}}` tokens anywhere in it; they are replaced per job
    when jobs are generated.
 3. **Add your data files.** On the Files tab, press **+** (or Add file) for
    every file that should be copied into each generated job directory — for
    example your experiment's JSON/YAML parameters. Your source files are never
    modified; only the copies inside each job directory reflect your edits.
 4. **Express a sweep (optional).** Either replace a scalar value with an
    **array** in an included data file (e.g. `{ "CodeLength": [256, 128, 23] }`),
    or write an inline `[[...]]` sweep in the job body (e.g.
    `--lr [[0.001, 0.01, 0.1]]`). Every such sweep becomes a dimension and all
    dimensions combine via **Cartesian product**, so one job is generated per
    combination. The sweeps that will expand are highlighted in the editor.
 5. **Inspect the results.** Open the **Preview** tab: an interactive tree shows
    every generated job directory; select any node to read the file. Confirm the
    header, the token substitutions and the sweep combinations all look right
    before writing anything to disk.
 6. **Choose your concurrency.** In the Preview pane, set **Max parallel jobs**
    to control how many jobs run at once when you submit: `0` = no limit (all
    parallel), `1` = all in series, `N` = `N` parallel chains. Dependencies are
    created so each job in a chain waits for the previous one to succeed.
 7. **Write & Submit.** Press **Write & Submit** to write the job directories to
    disk and launch them with the scheduler (`sbatch` for SLURM, `qsub` for
    PBS). If any target already exists and would be overwritten, the Preview
    pane blocks the write and lists the conflicts — resolve them or press
    **Override conflicts** to write anyway. Your settings are autosaved along
    the way.
 8. **Follow up.** If any submission fails, a **log modal** opens with the
    details — use it to diagnose rejections. The **Jobs queue** tab lists your
    running scheduler jobs, with each job's **state colour-coded** (green
    running, yellow pending, red failed/cancelled) and a **progress bar** for
    SLURM jobs that have a time limit. Filter the list by typing in the
    **filter** box, re-sort it with the **sort** dropdown, select a row and press
    **Details** to inspect the full scheduler output (`scontrol show job` /
    `qstat -f`), or **Cancel** to stop the selected job (`scancel` / `qdel`).
    Revisit **Log** in the Preview pane to reread the last submission's log at
    any time.
 9. **Quit.** Press **Ctrl+X** (or **Escape**) to exit; your settings are
    already saved.

## Settings tab

- **Job root directory** — base directory under which generated job
  subdirectories are written. Each job gets its own numbered subdirectory.
  When the entered directory does not exist yet, a **Create** button appears
  next to the field — press it to create the directory (and any missing
  parents). The button is hidden once the directory exists.
- **Job name** — optional. Replaces the root-basename prefix in the scheduler
  `--job-name` / `-N` field. In multi-job mode the per-job id is appended
  (`<job name>-<id>`); leave empty to use the root's basename as the prefix.
- **Single job in-place** — check to write **one** job directly in the job root
  directory (no numbered subdirectory) and disable sweep expansion; `{{JOBID}}`
  expands to the job name (or the root's basename when blank). Any active sweep
  dimensions are ignored with a warning. Included files are written directly
  into the job root, so same-named files there are overwritten.
- **Email (job notifications)** — full address (e.g. `you@example.com`)
  used in the scheduler `--mail-user` / `-M` header field. Leave empty to omit
  the mail lines from the header.
- **Email notifications (SLURM)** — a comma-separated `--mail-type` list
  (e.g. `ALL`, or `BEGIN,FAIL`) used when the email address is set; leave
  empty to emit `NONE`. Valid events: `ALL, NONE, BEGIN, END, FAIL, REQUEUE,
  TIME_LIMIT`.
- **Scheduler header** — generated from your settings; shown read-only so you
  can verify it before writing.
- **Job body** — the content placed right after the scheduler header (any
  remaining scheduler directives plus the command script). Use **Import job
  from file** or **Import job from text** to fill it from an existing SLURM/PBS
  job, or type it directly. **Clear** resets the job root, email and job body.

## Tokens

The job body accepts two placeholders, replaced per job when jobs are
generated:

- `{{JOBID}}` — the generated job's id.
- `{{JOBDIR}}` — the generated job's directory path.

Known tokens are highlighted in **yellow**; unknown tokens (typos) are
highlighted in **red** and reported in the status bar at the bottom, clearing
once you fix them.

## Included files and directories

Use the **+** button in the tab bar to add files (or whole directories) that
are copied into each generated job directory. Each included item gets its own
tab. Only the **copy** in the job directory changes when you edit; your source
files are never modified.

- **Files** are **editable inline**. Turn on **show diff** to see the unified
  diff (source vs. your edits) side-by-side as you type. **Binary** files are
  detected automatically and are **not editable**; they are shown as a read-only
  pane (the same layout as a symlinked file), regardless of whether they are
  copied or symlinked.
- Every item has a **symlink (save space)** toggle:
  - **Off (copy)** — the default for files; the file is copied into each job
    directory so it can be edited and swept.
  - **On (symlink)** — the default for directories; the item is **symlinked**
    into each job directory rather than copied, sharing the same source across
    jobs and saving space. **Symlinked files are not editable and not swept**.
  - Directories are always copied/symlinked whole (never edited).


## Sweeps

There are two ways to express a sweep, and both feed the **same** Cartesian
product: one job is generated per combination of every sweep dimension.

### 1. Data-file sweeps (JSON / YAML)

An included data file (`.json`, `.yaml` or `.yml`) is swept when a key's value
is an **array** that replaces a scalar. The on-disk source keeps the single
scalar; express the sweep by editing the file's copy and replacing that scalar
with an array of the values to try.

Keys may be top-level or **nested** (a dotted path such as
`data.random_linear_transform`). Text (string) values can be swept just like
numbers.

JSON example — sweep `CodeLength` over 256, 128, 23:

    { "CodeLength": [256, 128, 23] }

JSON example — sweep `model` over the strings "resnet", "densenet", "unet":

    { "model": ["resnet", "densenet", "unet"] }

YAML example — sweep the nested `data.random_linear_transform`:

    data:
      random_linear_transform: [0.1, 0.3, 0.5]

Every array across every included data file becomes a dimension; all dimensions
combine via **Cartesian product**, producing one job per combination. The
arrays that will be expanded are **highlighted** in the file view; a malformed
data file is flagged in the status bar.

Arrays that are already arrays in the source (genuine data, e.g. a fixed list)
are **not** swept, and neither are arrays of mappings.

### 2. Job-body (script) sweeps — `[[...]]`

The job body itself can be swept by writing an inline `[[...]]` region
containing a **JSON array of scalar values** (numbers, strings, or `true` /
`false`). The `[[...]]` is a dimension just like a data-file array: each
generated job receives one value from the list, with the `[[...]]` replaced by
that value rendered **bare** (no quotes).

Example — sweep the learning rate over 0.001, 0.01 and 0.1:

    python train.py --lr [[0.001, 0.01, 0.1]]

This generates one job per value, running `--lr 0.001`, `--lr 0.01` and
`--lr 0.1` respectively. Strings are substituted unquoted (e.g.
`--model [[resnet, densenet, unet]]` runs `--model resnet`, …) and booleans as
`true` / `false`.

Valid `[[...]]` sweeps are highlighted in **cyan** in the job-body editor;
**malformed** ones (invalid JSON, non-scalar contents, or an unclosed `[[`)
are highlighted in **red** and reported in the status bar until fixed.

Both kinds of sweep combine into the same Cartesian product — a data-file sweep
and a body sweep together produce one job per pair of values.

## Parallelism limit

In the Preview pane, on the same line as **Write & Submit**, a **Max parallel
jobs** field sets how many jobs run **in parallel** when you submit:

- **0** (default) — no limit; all jobs can run in parallel.
- **1** — all jobs run in series (one chain).
- **N** — `N` parallel chains are launched; within each chain jobs run in
  series, so at most `N` jobs run at once.

Job dependencies are created at submission time with the scheduler's
dependency mechanism (`--dependency=afterok` for SLURM, `-W depend=afterok`
for PBS): each job in a chain waits for the previous one to finish
successfully before starting. The value is saved with your settings.

## Submission log

After **Write & Submit**, a per-job **submission log** is kept: for every job
it records the command, exit code, and the scheduler's stdout/stderr (or the
error if it could not be launched). When at least one job fails, a **log
modal** opens automatically showing these details — use it to see why a
submission was rejected (e.g. an allocation error) instead of just a failed
count. The same log is written to `submit.log` under the job root directory so
you can inspect it outside the app. The **Log** button in the Preview pane
reopens the log of the last submission at any time.

## Jobs queue

The **Jobs queue** tab lists the current OS user's active scheduler jobs,
showing each job's id, name, state and elapsed time. It queries
`squeue --format=%i|%u|%j|%T|%M|%l` on SLURM clusters or `qstat` on PBS, then
filters to the current user client-side when the "my jobs only" checkbox is
on.

Each row is **colour-coded by state** (green running, yellow pending, cyan
completing, magenta cancelled, red failed) and shows a **progress bar**
(`elapsed / time-limit`) for SLURM jobs that have a time limit; PBS jobs show
their elapsed time only.

Type in the **filter** box to show only rows whose id, name, state or time
matches the text, and use the **sort** dropdown to order by id, name, state or
time. Select a row and press:

- **Details** — open a modal with the full scheduler output for that job
  (`scontrol show job` on SLURM, `qstat -f` on PBS).
- **Cancel** — confirm, then stop the selected job (`scancel` on SLURM, `qdel`
  on PBS).

Press **Refresh** (or open the tab) to update the list; it is updated manually
(no automatic polling). If no scheduler is detected, the pane reports
"No scheduler found".

## Presets

Save your current settings under a name, and later load or delete them, via the
**Presets** button (top left).

## Preview / Write & Submit

- **Preview** tab — inspect the generated job directories before writing. If
  any write target already exists on disk and would be overwritten, a warning
  bar lists each conflict (a file, directory copy/symlink, or the job file). A
  job directory that already exists as a *file* is always flagged, because it
  cannot be created. Writing is blocked until you resolve the conflicts or
  press **Override conflicts** to write anyway.
- **Write & Submit** — write the directories and submit them to the scheduler
  (`sbatch` for SLURM, `qsub` for PBS).

## Screenshots

The in-app Help cannot display images; screen captures of each tab (Settings,
Files, Preview and the Jobs queue) are available as SVGs in the `screenshots/`
directory of the repository.

## Saving

Settings and inline edits are **autosaved** to `~/.config/forgebatch/config.json`
shortly after you type; press **Ctrl+S** to save them immediately. Press
**Ctrl+X** (or **Escape**) on the main screen to quit — **Ctrl+X** works from
any dialog too. Pass `-c/--config PATH` on the command line to use a different
configuration file.
