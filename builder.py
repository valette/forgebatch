"""builder.py - pure job-generation logic for forgebatch.

No Textual dependency. Everything here is unit-testable in isolation.

Job model:
  * A job dir is built from a set of "included files". Each included file may
    be edited inline; edits only ever affect the copy written into the job dir,
    never the source file on disk.
  * The "include script" file (configured in the Settings tab) is appended
    verbatim at the end of the programmatic job file (after the scheduler
    header) and is *not* copied as a separate file.
  * A sweep applies to every included *data* file (`.json`, `.yaml`, `.yml`)
    whose content parses as a mapping: any array on a numeric/string key
    becomes a sweep dimension scoped to that file and key. Keys may be nested
    (dotted paths such as `data.random_linear_transform`). All dimensions
    across all data files combine via Cartesian product, producing one job per
    combination. A key holding a single scalar can be swept by replacing that
    scalar with an array. Arrays of mappings are never swept.
"""

import copy
import json
import os
import re
from collections import OrderedDict

try:
    import yaml
    HAS_YAML = True
except ImportError:  # PyYAML is a hard dependency; degrade to plain copies.
    yaml = None
    HAS_YAML = False

import config
import diffing
from config import next_job_id
from fileio import read_text_or_bytes

join = os.path.join

TOKEN_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

KNOWN_TOKENS = frozenset({"JOBID", "JOBDIR"})


def substitute_placeholders(text, values):
    """Replace {{TOKEN}} placeholders in include-job/script content.

    values maps a token name (e.g. "JOBID", "JOBDIR") to the per-job value.
    Unknown tokens are left untouched.
    """
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def unknown_tokens(text, known=KNOWN_TOKENS):
    """Sorted distinct {{TOKEN}} names in text not present in `known`."""
    found = set()
    for name in TOKEN_RE.findall(text or ""):
        if name not in known:
            found.add(name)
    return sorted(found)


def _body_sweep_scalar(v):
    """True if v is a value a job-body [[...]] sweep may hold (incl. bool)."""
    return isinstance(v, (int, float, str, bool))


def _body_sweep_spans(text):
    """Locate [[...]] sweep regions in a job body.

    Returns a list of (start, end, inner_start, inner_end, ok) where start/end
    bound the full [[...]] region, inner_start/inner_end bound the inner JSON
    array (brackets included), and ok is True when the inner content parses as
    a JSON array of scalar values. Invalid or unclosed regions still produce a
    span (so they can be highlighted red)."""
    spans = []
    i = 0
    n = len(text)
    while i < n - 1:
        idx = text.find("[[", i)
        if idx == -1:
            break
        inner_start = idx + 1
        inner_end = _match_flow(text, inner_start, "[", "]")
        closed = (inner_end > inner_start + 1
                  and inner_end <= n and text[inner_end - 1] == "]"
                  and inner_end < n and text[inner_end] == "]")
        if closed:
            sweep_end = inner_end + 1
            content = text[idx + 1:inner_end]
            try:
                values = json.loads(content)
            except ValueError:
                values = None
            ok = isinstance(values, list) and all(
                _body_sweep_scalar(v) for v in values)
            spans.append((idx, sweep_end, idx + 1, inner_end, ok))
            i = sweep_end
        else:
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = n
            spans.append((idx, line_end, idx + 1, line_end, False))
            i = line_end
    return spans


def collect_body_sweeps(body):
    """OrderedDict[sweep_text -> values] of valid [[...]] sweeps in the body."""
    text = body or ""
    sweeps = OrderedDict()
    for start, end, inner_start, inner_end, ok in _body_sweep_spans(text):
        if not ok:
            continue
        sweeps.setdefault(text[start:end], json.loads(text[inner_start:inner_end]))
    return sweeps


def body_sweep_ranges(body):
    """Return (line, start_col, end_col, is_valid) spans for [[...]] sweeps in
    the job body: valid scalar arrays (True) or malformed/invalid ones (False)."""
    text = body or ""
    out = []
    for start, end, _inner_start, _inner_end, ok in _body_sweep_spans(text):
        for line, s, e in _flow_ranges(text, start, end):
            out.append((line, s, e, ok))
    return out


def body_sweep_errors(body):
    """Human-readable messages for each malformed/invalid [[...]] sweep."""
    text = body or ""
    errors = []
    for start, end, _inner_start, _inner_end, ok in _body_sweep_spans(text):
        if not ok:
            snippet = text[start:end]
            if len(snippet) > 40:
                snippet = snippet[:40] + "..."
            errors.append("malformed script sweep: " + snippet)
    return errors


def _render_sweep_value(v):
    """Render a single sweep value inline (bare strings, JSON-lowercase bools)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    return str(v)


def is_scalar(v):
    return isinstance(v, (int, float, str)) and not isinstance(v, bool)


def job_chains(n, parallelism):
    """Partition job indices 0..n-1 into `parallelism` round-robin chains so
    that at most `parallelism` jobs run concurrently. Returns [] when no
    dependencies are needed (n <= 1 or parallelism <= 0). Each chain is a list
    of indices that must run in series; chains run in parallel."""
    if n <= 1 or parallelism <= 0:
        return []
    chains = [[] for _ in range(parallelism)]
    for i in range(n):
        chains[i % parallelism].append(i)
    return [c for c in chains if c]


def dependency_flags(scheduler, dep_id):
    """CLI args making a job wait for dep_id to complete successfully."""
    if not dep_id:
        return []
    if scheduler == "slurm":
        return ["--dependency=afterok:" + dep_id]
    return ["-W", "depend=afterok:" + dep_id]


def job_name_prefix(fields):
    """The job-name base for a run: the settable `job_name`, else the root's
    basename. In multi-job mode `-<id>` is appended; single-job mode uses it
    verbatim."""
    root = (fields.get("job_root") or "").strip()
    name = (fields.get("job_name") or "").strip()
    return name or (os.path.basename(os.path.abspath(root)) if root else config.name)


def job_header(fields, job_id, job_path):
    """Scheduler/job parameters, generated programmatically (SLURM or PBS)."""
    use_slurm = config.use_slurm
    name = job_name_prefix(fields)
    lines = ["#!/bin/sh"]
    if use_slurm:
        lines.append("#SBATCH --job-name=" + name + ("-" + job_id if job_id else ""))
        if fields.get("email"):
            types = fields.get("mail_type")
            if types is None:
                types = ["ALL"]
            if not types:
                types = ["NONE"]
            lines.append("#SBATCH --mail-type=" + ",".join(types))
            lines.append("#SBATCH --mail-user=" + fields["email"])
        lines.append("#SBATCH --output=" + join(job_path, "log.out"))
        lines.append("#SBATCH --error=" + join(job_path, "log.out"))
    else:
        lines.append("#PBS -N " + name + ("-" + job_id if job_id else ""))
        lines.append("#PBS -o " + join(job_path, "log.out"))
        lines.append("#PBS -e " + join(job_path, "log.err"))
        if fields.get("email"):
            lines.append("#PBS -m ae")
            lines.append("#PBS -M " + fields["email"])
    return lines


def parse_import(content, use_slurm):
    """Parse an imported scheduler job file into its reusable pieces.

    Returns (email, mail_types, body):
      * email - the address from the --mail-user / -M line ('' if absent)
      * mail_types - the comma-separated --mail-type values (SLURM only; []
        for PBS or when absent)
      * body  - the whole job after the header: every line (remaining
        scheduler directives and the command script, kept in original order)
        with the lines the generated header owns (job-name, mail-type,
        mail-user, output, error for SLURM; -N, -m, -M, -o, -e for PBS)
        removed and the leading #! shebang dropped.
    """
    prefix = "#SBATCH" if use_slurm else "#PBS"
    if use_slurm:
        header_keys = {"--job-name", "--mail-type", "--mail-user", "--output", "--error"}
    else:
        header_keys = {"-N", "-m", "-M", "-o", "-e"}
    body = []
    email = ""
    mail_types = []
    for raw in content.split("\n"):
        line = raw.lstrip()
        if not line.startswith(prefix):
            body.append(raw)
            continue
        directive = line[len(prefix):].strip()
        if use_slurm:
            key = directive.split("=", 1)[0].split(None, 1)[0] if directive else ""
            if key == "--mail-user":
                email = (directive.split("=", 1)[1].strip() if "=" in directive
                         else (directive.split(None, 1)[1].strip()
                               if len(directive.split(None, 1)) > 1 else ""))
            elif key == "--mail-type":
                rest = (directive.split("=", 1)[1] if "=" in directive
                        else (directive.split(None, 1)[1]
                              if len(directive.split(None, 1)) > 1 else ""))
                mail_types = [t.strip() for t in rest.split(",") if t.strip()]
            if key in header_keys:
                continue
        else:
            flag = directive.split(None, 1)[0] if directive else ""
            if flag == "-M":
                email = directive[len(flag):].strip()
            if flag in header_keys:
                continue
        body.append(line)
    if body and body[0].lstrip().startswith("#!"):
        body.pop(0)
    return email, mail_types, "\n".join(body).strip("\n")


def _read_resolved(path, edits):
    """Return (content, is_binary, error_or_None).

    Content comes from a stored edit (unified diff or legacy full-text
    override) when present, otherwise from the file on disk. Shared by the
    include-job, include-script, and included-file paths so they resolve
    content identically."""
    edited = edits.get(path)
    if edited is not None:
        text, is_binary, _conflicts = diffing.resolve_edit(path, edited)
        return text, is_binary, None
    if not path:
        return "", False, None
    if not os.path.exists(path):
        return None, False, "not found: " + path
    content, is_binary, err = read_text_or_bytes(path)
    if err:
        return None, False, "cannot read: " + err
    return content, is_binary, None


def _is_data_path(path):
    """True if path names a sweepable data file (JSON or YAML)."""
    return str(path).lower().endswith((".json", ".yaml", ".yml"))


def _parse_data(path, text):
    """Parse `text` as JSON or YAML by extension.

    Returns (obj, kind) where kind is "json"/"yaml", or (None, None) if the
    content does not parse as a mapping (or YAML support is unavailable)."""
    if not isinstance(text, str):
        return None, None
    try:
        if _is_data_path(path):
            if str(path).lower().endswith((".yaml", ".yml")):
                if not HAS_YAML:
                    return None, None
                obj = yaml.safe_load(text)
                kind = "yaml"
            else:
                obj = json.loads(text)
                kind = "json"
        else:
            return None, None
    except (ValueError, yaml.YAMLError):
        return None, None
    if not isinstance(obj, dict):
        return None, None
    return obj, kind


def _load_base(path):
    """Read the on-disk base mapping for a data file, or {} on any error."""
    base = {}
    content, is_binary, _err = read_text_or_bytes(path)
    if content is None or is_binary:
        return base
    obj, _kind = _parse_data(path, content)
    return obj if isinstance(obj, dict) else base


def value_at(obj, path):
    """Value at a dotted-key path tuple within a nested mapping, else None."""
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def set_at(obj, path, value):
    """Set `value` at a dotted-key path tuple within a nested mapping."""
    cur = obj
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value


def collect_dims(obj, base, prefix=()):
    """Collect sweep dimensions from a parsed data mapping.

    Returns {path_tuple: [values, ...]} for arrays whose on-disk base value at
    the same path is a scalar, or whose path is absent from the base and whose
    elements are all scalars. Walks nested mappings (dotted paths) but never
    descends into arrays of mappings. Unifies JSON and YAML (both are just
    nested mappings after parsing)."""
    dims = {}
    for key, value in obj.items():
        path = prefix + (key,)
        if isinstance(value, dict):
            dims.update(collect_dims(value, base, path))
        elif isinstance(value, list):
            base_val = value_at(base, path)
            if is_scalar(base_val) or (base_val is None and value
                                       and all(is_scalar(v) for v in value)):
                dims[path] = value
    return dims


def _data_entry(path, content):
    """If content parses as a JSON/YAML mapping, return (obj, dims, kind)
    where dims maps path-tuple -> list of values for swept keys. Otherwise
    None."""
    obj, kind = _parse_data(path, content)
    if obj is None:
        return None
    base = _load_base(path)
    dims = collect_dims(obj, base)
    for path_key, values in dims.items():
        set_at(obj, path_key, values[0] if values else values)
    return obj, dims, kind


def validate_data(path, content):
    """Return an error message if a data file does not parse, else None."""
    if not _is_data_path(path):
        return None
    if str(path).lower().endswith((".yaml", ".yml")) and not HAS_YAML:
        return "PyYAML not installed; cannot sweep YAML files"
    obj, _kind = _parse_data(path, content)
    if obj is None:
        return "malformed " + ("YAML" if str(path).lower().endswith((".yaml", ".yml")) else "JSON")
    return None


def sweep_value_spans(path, content):
    """Return (line, start_col, end_col) ranges for active sweep array values.

    Keys may be nested (dotted paths); only arrays that actually become sweep
    dimensions are reported, so the file view highlights exactly what will be
    expanded. A YAML block list or a multi-line flow array yields one range per
    covered line."""
    entry = _data_entry(path, content)
    if entry is None or not entry[1]:
        return []
    spans = []
    for path_key in entry[1]:
        spans.extend(_path_ranges(content, path_key))
    return spans


def _path_ranges(text, path):
    """Locate display ranges for an array value at a nested key path."""
    regions = [(0, len(text))]
    for key in path:
        is_leaf = (key == path[-1])
        acc = []
        for (s, e) in regions:
            seg = text[s:e]
            for (vs, ve, kind) in _value_regions(seg, key):
                if is_leaf:
                    acc.extend(_display_ranges(text, s + vs, s + ve, kind))
                else:
                    acc.append((s + vs, s + ve))
        if is_leaf:
            return acc
        regions = acc
    return []


def _value_regions(text, key):
    """Return (start, end, kind) char spans of values for `key` at the current
    level of `text`. kind is flow_array / flow_object / scalar / block_list /
    block_map."""
    regions = []
    key_re = re.compile(r'"?%s"?\s*:' % re.escape(key))
    for match in key_re.finditer(text):
        j = match.end()
        while j < len(text) and text[j] in " \t":
            j += 1
        rest = text[j:]
        nl = rest.find("\n")
        inline = rest[: nl if nl != -1 else len(rest)].rstrip()
        if inline.startswith("["):
            regions.append((j, _match_flow(text, j, "[", "]"), "flow_array"))
        elif inline.startswith("{"):
            regions.append((j, _match_flow(text, j, "{", "}"), "flow_object"))
        elif inline:
            regions.append((j, j + len(inline), "scalar"))
        else:
            line = text.count("\n", 0, j)
            regions.append((j, _block_end(text, line), _block_kind(text, line)))
    return regions


def _block_lines(text, key_line):
    """Indices of YAML block lines following `key_line` (more indented than it)."""
    lines = text.split("\n")
    key_indent = len(lines[key_line]) - len(lines[key_line].lstrip())
    out = []
    i = key_line + 1
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= key_indent:
            break
        out.append(i)
        i += 1
    return out


def _block_kind(text, key_line):
    for ln in _block_lines(text, key_line):
        if text.split("\n")[ln].lstrip().startswith("- "):
            return "block_list"
        return "block_map"
    return "block_map"


def _block_end(text, key_line):
    """Char offset just past the last YAML block line after `key_line`."""
    lines = _block_lines(text, key_line)
    if not lines:
        return 0
    all_lines = text.split("\n")
    offsets = []
    acc = 0
    for ln in all_lines:
        offsets.append(acc)
        acc += len(ln) + 1
    return offsets[lines[-1]] + len(all_lines[lines[-1]])


def _match_flow(text, open_idx, open_ch, close_ch):
    """Return the index just past the matching `close_ch` for the delimiter at
    open_idx (handling nesting and quoted strings)."""
    depth = 0
    in_str = None
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in "\"'":
                in_str = ch
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return open_idx + 1


def _display_ranges(text, start, end, kind):
    if kind == "block_list":
        return _block_list_ranges(text, start, end)
    return _flow_ranges(text, start, end)


def _char_line_col(text, idx):
    line = text.count("\n", 0, idx)
    col = idx - (text.rfind("\n", 0, idx) + 1)
    return line, col


def _flow_ranges(text, start, end):
    """Per-line (line, start_col, end_col) ranges covering a flow value."""
    s_line, s_col = _char_line_col(text, start)
    e_line, e_col = _char_line_col(text, end)
    lines = text.split("\n")
    if s_line == e_line:
        return [(s_line, s_col, e_col)]
    out = [(s_line, s_col, len(lines[s_line]))]
    for ln in range(s_line + 1, e_line):
        out.append((ln, 0, len(lines[ln])))
    out.append((e_line, 0, e_col))
    return out


def _block_list_ranges(text, start, end):
    """Per-item (line, start_col, end_col) ranges for a YAML block list."""
    lines = text.split("\n")
    s_line, _ = _char_line_col(text, start)
    e_line, _ = _char_line_col(text, end)
    out = []
    for ln in range(s_line, min(e_line + 1, len(lines))):
        raw = lines[ln]
        if raw.lstrip().startswith("-"):
            out.append((ln, raw.find("-"), len(raw)))
    return out


def build_jobs(fields):
    """Generate one job per sweep combination across all included JSON files.

    fields keys: job_root, email, job_body,
    included_files (list of {path}), included_dirs (list of {path, mode}),
    file_edits (dict path -> text).

    The job file is assembled as: scheduler header, then the job body.

    Returns (variants, error_or_None, warnings). Each variant is a dict with
    keys: job, job_file, job_path, label, files (dict basename -> str|bytes
    content), dirs (list of {path, basename, kind, mode}). `warnings` is a
    list of human-readable strings (e.g. unknown {{TOKEN}} placeholders left
    unreplaced).
    """
    job_root_raw = fields.get("job_root") or ""
    job_root = os.path.abspath(job_root_raw) if job_root_raw else ""
    file_edits = fields.get("file_edits") or {}
    job_body = str(fields.get("job_body") or "").rstrip("\n")

    unknown = unknown_tokens(job_body)
    warnings = (["unknown token(s) in job body: "
                 + ", ".join("{{" + t + "}}" for t in unknown)] if unknown else [])
    warnings.extend(body_sweep_errors(job_body))
    if not job_root:
        return [], "jobs root is not set", warnings

    entries = []  # each: dict with path, basename, kind, obj/dims or content
    file_links = []  # symlinked files: {"path", "basename"} (never edited/swept)
    for item in fields.get("included_files") or []:
        path = (item.get("path") if isinstance(item, dict) else item)
        path = str(path).strip()
        if not path:
            continue
        mode = (item.get("mode") if isinstance(item, dict) else "") or "copy"
        if mode == "symlink":
            if not os.path.exists(path):
                warnings.append("file " + path + ": not found")
                continue
            file_links.append({"path": path, "basename": os.path.basename(path)})
            continue
        if not os.path.exists(path) and path not in file_edits:
            # Deliberate: missing files without a stored edit are skipped
            # silently (see tests/test_builder.py#missing_included_file_is_skipped_without_warning).
            continue
        content, is_binary, err = _read_resolved(path, file_edits)
        if content is None:
            if err:
                warnings.append("file " + path + ": " + err)
            continue
        entry = {"path": path, "basename": os.path.basename(path)}
        jj = _data_entry(path, content)
        if jj is not None:
            entry["kind"] = jj[2]
            entry["obj"], entry["dims"] = jj[0], jj[1]
        else:
            entry["kind"] = "file"
            entry["content"] = content
        entries.append(entry)

    # directories are never edited or swept; they are copied/symlinked into
    # each job directory as-is (mode: "copy" or "symlink", default symlink).
    dirs = []
    for item in fields.get("included_dirs") or []:
        path = (item.get("path") if isinstance(item, dict) else item)
        path = str(path).strip()
        if not path:
            continue
        if not os.path.isdir(path):
            warnings.append("directory " + path + ": not found")
            continue
        mode = (item.get("mode") if isinstance(item, dict) else "") or "symlink"
        dirs.append({"path": path, "basename": os.path.basename(path),
                     "kind": "dir", "mode": mode})

    # Warn when two included items would write to the same filename in each job
    # directory (e.g. a/config.json and b/config.json both become config.json),
    # since the last one silently wins.
    seen_names = {}
    for e in entries + file_links + dirs:
        b = e["basename"]
        if b and b in seen_names and seen_names[b] != e["path"]:
            warnings.append("multiple included items write '" + b
                            + "' in each job dir")
        seen_names[b] = e["path"]

    # global sweep dimensions: (path, key_path_tuple) -> list of values
    dims = {}
    for e in entries:
        if e["kind"] in ("json", "yaml") and e["dims"]:
            for key_path, values in e["dims"].items():
                dims[(e["path"], key_path)] = values
    body_sweeps = collect_body_sweeps(job_body)
    for sweep_text, values in body_sweeps.items():
        dims[("__body__", (sweep_text,))] = values

    single = bool(fields.get("single_job"))
    combos = [{}]
    if single:
        if dims:
            warnings.append(
                "single-job mode: ignoring %d sweep dimension(s)" % len(dims))
    else:
        for (path, key_path), values in dims.items():
            if not values:
                label = ".".join(str(k) for k in key_path)
                src = "script sweep" if path == "__body__" else os.path.basename(path)
                warnings.append(f"empty sweep for {src}: {label}")
                continue
            new_combos = []
            for c in combos:
                for v in values:
                    cc = dict(c)
                    cc[(path, key_path)] = v
                    new_combos.append(cc)
            combos = new_combos

    results = []
    job_counter = int(next_job_id(job_root)) if not single else None
    for combo in combos:
        files = {}
        for e in entries:
            if e["kind"] in ("json", "yaml"):
                obj = copy.deepcopy(e["obj"])
                for (path, key_path), value in combo.items():
                    if path == e["path"]:
                        set_at(obj, key_path, value)
                if e["kind"] == "yaml":
                    content = yaml.safe_dump(obj, default_flow_style=False,
                                             sort_keys=False)
                else:
                    content = json.dumps(obj, indent=4)
            else:
                content = e["content"]
            files[e["basename"]] = content

        if single:
            job_id = job_name_prefix(fields)
            job_path = job_root
        else:
            job_id = str(job_counter)
            job_counter += 1
            job_path = join(job_root, job_id)
        header = "\n".join(job_header(fields, "" if single else job_id, job_path))
        body = substitute_placeholders(job_body, {"JOBID": job_id, "JOBDIR": job_path})
        for (path, key_path), value in combo.items():
            if path == "__body__":
                body = body.replace(key_path[0], _render_sweep_value(value))
        job = (header + ("\n" + body if body else "")) + "\n"
        job_file = join(job_path, "job." + ("slurm" if config.use_slurm else "pbs"))
        label_parts = []
        for (path, key_path), value in combo.items():
            if path == "__body__":
                label_parts.append("%s:%s" % (key_path[0], _render_sweep_value(value)))
            else:
                label_parts.append(
                    "%s.%s:%s" % (os.path.basename(path), ".".join(key_path),
                                  json.dumps(value) if not isinstance(value, str) else value))
        label = ",".join(label_parts) or "base"
        results.append({"job": job, "job_file": job_file, "job_path": job_path,
                        "label": label, "files": files, "dirs": list(dirs),
                        "file_links": list(file_links)})
    return results, None, warnings


def existing_targets(variants):
    """Return write targets that already exist on disk and would be clobbered.

    Each entry is {"kind", "path"}. `os.path.lexists` is used so a dangling
    symlink is also flagged (its target path cannot be created). A job dir that
    already exists as a directory is NOT a conflict (single-job in-place writes
    into the root by design); only the targets inside it are checked. A job dir
    that exists as a *file* is flagged because it cannot be created."""
    conflicts = []
    for v in variants:
        job_path = v["job_path"]
        if os.path.lexists(job_path) and not os.path.isdir(job_path):
            conflicts.append({"kind": "job dir is a file", "path": job_path})
        targets = [("job file", v["job_file"])]
        targets += [("file", join(job_path, b)) for b in v.get("files") or {}]
        for d in v.get("dirs") or []:
            kind = "dir copy" if d.get("mode") == "copy" else "dir symlink"
            targets.append((kind, join(job_path, d["basename"])))
        for fl in v.get("file_links") or []:
            targets.append(("file symlink", join(job_path, fl["basename"])))
        seen = set()
        for kind, path in targets:
            if path in seen:
                continue
            seen.add(path)
            if os.path.lexists(path):
                conflicts.append({"kind": kind, "path": path})
    return conflicts
